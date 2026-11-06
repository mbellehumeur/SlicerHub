"""Export visible Slicer models/segments to GLB and build Hub glb-send messages.

MetaQuest mesh path (not LiveScene). Inspired by SlicerRemoteView's GLB snapshot
idea; implemented here without that dependency.
"""

from __future__ import annotations

import hashlib
import json
import logging
import struct
from typing import Any, Dict, List, Optional, Sequence, Tuple

LOGGER = logging.getLogger("HubInterface.ImageDisplay.Mesh")

GLB_MAGIC = 0x46546C67
GLB_VERSION = 2
CHUNK_JSON = 0x4E4F534A
CHUNK_BIN = 0x004E4942
FLOAT = 5126
UNSIGNED_INT = 5125
ARRAY_BUFFER = 34962
ELEMENT_ARRAY_BUFFER = 34963
TRIANGLES = 4

# LPS (Slicer) → glTF (+Y up): (x, y, z)_gltf = (x, z, -y)_lps
_LPS_TO_GLTF = (
    1.0,
    0.0,
    0.0,
    0.0,
    0.0,
    0.0,
    -1.0,
    0.0,
    0.0,
    1.0,
    0.0,
    0.0,
    0.0,
    0.0,
    0.0,
    1.0,
)


def _pad4(n: int) -> int:
    return (4 - (n % 4)) % 4


def _align4(buf: bytearray) -> None:
    buf.extend(b"\x00" * _pad4(len(buf)))


def _pack_glb(gltf: Dict[str, Any], bin_chunk: bytes) -> bytes:
    json_bytes = json.dumps(gltf, separators=(",", ":"), ensure_ascii=True).encode(
        "utf-8"
    )
    json_pad = _pad4(len(json_bytes))
    json_bytes = json_bytes + (b" " * json_pad)
    bin_pad = _pad4(len(bin_chunk))
    bin_padded = bin_chunk + (b"\x00" * bin_pad)
    total = 12 + 8 + len(json_bytes) + 8 + len(bin_padded)
    out = bytearray()
    out.extend(struct.pack("<III", GLB_MAGIC, GLB_VERSION, total))
    out.extend(struct.pack("<II", len(json_bytes), CHUNK_JSON))
    out.extend(json_bytes)
    out.extend(struct.pack("<II", len(bin_padded), CHUNK_BIN))
    out.extend(bin_padded)
    return bytes(out)


def _mesh_to_arrays(polydata) -> Optional[Tuple[List[float], List[float], List[int]]]:
    if polydata is None or polydata.GetNumberOfPoints() < 3:
        return None
    triangulated = polydata
    try:
        import vtk

        if polydata.GetPolys().GetMaxCellSize() != 3 or polydata.GetStrips().GetNumberOfCells() > 0:
            filt = vtk.vtkTriangleFilter()
            filt.SetInputData(polydata)
            filt.Update()
            triangulated = filt.GetOutput()
        normals = vtk.vtkPolyDataNormals()
        normals.SetInputData(triangulated)
        normals.ComputePointNormalsOn()
        normals.SplittingOff()
        normals.Update()
        triangulated = normals.GetOutput()
    except Exception as exc:
        LOGGER.debug("mesh normal/tri filter skipped: %s", type(exc).__name__)

    n_pts = triangulated.GetNumberOfPoints()
    if n_pts < 3:
        return None
    positions: List[float] = []
    norms: List[float] = []
    point_normals = triangulated.GetPointData().GetNormals()
    for i in range(n_pts):
        x, y, z = triangulated.GetPoint(i)
        # LPS mm → glTF metres (+Y up)
        positions.extend([x * 0.001, z * 0.001, -y * 0.001])
        if point_normals is not None:
            nx, ny, nz = point_normals.GetTuple(i)
            norms.extend([nx, nz, -ny])
        else:
            norms.extend([0.0, 1.0, 0.0])

    indices: List[int] = []
    polys = triangulated.GetPolys()
    if polys is not None:
        try:
            import vtk

            ids = vtk.vtkIdList()
            polys.InitTraversal()
            while polys.GetNextCell(ids):
                if ids.GetNumberOfIds() >= 3:
                    indices.extend([ids.GetId(0), ids.GetId(1), ids.GetId(2)])
        except Exception:
            return None
    if len(indices) < 3:
        return None
    return positions, norms, indices


def write_glb_from_entities(
    entities: Sequence[Dict[str, Any]],
) -> Tuple[bytes, Dict[str, Any]]:
    """Build one GLB from entity dicts.

    Each entity: ``nodeId``, ``name``, ``color`` [r,g,b], ``opacity``,
    ``positions``, ``normals``, ``indices`` (flat lists).
    """
    bin_data = bytearray()
    accessors: List[Dict[str, Any]] = []
    buffer_views: List[Dict[str, Any]] = []
    meshes: List[Dict[str, Any]] = []
    materials: List[Dict[str, Any]] = []
    nodes: List[Dict[str, Any]] = []
    node_state: Dict[str, Any] = {}
    root_children: List[int] = []

    def add_f32_view(values: Sequence[float], acc_type: str) -> int:
        _align4(bin_data)
        offset = len(bin_data)
        raw = b"".join(struct.pack("<f", float(v)) for v in values)
        bin_data.extend(raw)
        bv_index = len(buffer_views)
        buffer_views.append(
            {
                "buffer": 0,
                "byteOffset": offset,
                "byteLength": len(raw),
                "target": ARRAY_BUFFER,
            }
        )
        count = len(values) // (3 if acc_type == "VEC3" else 1)
        acc: Dict[str, Any] = {
            "bufferView": bv_index,
            "componentType": FLOAT,
            "count": count,
            "type": acc_type,
        }
        if acc_type == "VEC3" and count:
            xs = values[0::3]
            ys = values[1::3]
            zs = values[2::3]
            acc["min"] = [min(xs), min(ys), min(zs)]
            acc["max"] = [max(xs), max(ys), max(zs)]
        accessors.append(acc)
        return len(accessors) - 1

    def add_u32_index_view(values: Sequence[int]) -> int:
        _align4(bin_data)
        offset = len(bin_data)
        raw = b"".join(struct.pack("<I", int(v)) for v in values)
        bin_data.extend(raw)
        bv_index = len(buffer_views)
        buffer_views.append(
            {
                "buffer": 0,
                "byteOffset": offset,
                "byteLength": len(raw),
                "target": ELEMENT_ARRAY_BUFFER,
            }
        )
        accessors.append(
            {
                "bufferView": bv_index,
                "componentType": UNSIGNED_INT,
                "count": len(values),
                "type": "SCALAR",
            }
        )
        return len(accessors) - 1

    for ent in entities:
        node_id = str(ent.get("nodeId") or "")
        if not node_id:
            continue
        positions = ent.get("positions") or []
        normals = ent.get("normals") or []
        indices = ent.get("indices") or []
        if len(positions) < 9 or len(indices) < 3:
            continue
        if len(normals) != len(positions):
            normals = [0.0, 1.0, 0.0] * (len(positions) // 3)
        color = ent.get("color") or [0.8, 0.8, 0.8]
        opacity = float(ent.get("opacity") if ent.get("opacity") is not None else 1.0)
        visible = bool(ent.get("visible", True))
        pos_acc = add_f32_view(positions, "VEC3")
        nrm_acc = add_f32_view(normals, "VEC3")
        idx_acc = add_u32_index_view(indices)
        mat_index = len(materials)
        materials.append(
            {
                "pbrMetallicRoughness": {
                    "baseColorFactor": [
                        float(color[0]),
                        float(color[1]),
                        float(color[2]),
                        max(0.0, min(1.0, opacity)),
                    ],
                    "metallicFactor": 0.0,
                    "roughnessFactor": 0.6,
                },
                "alphaMode": "BLEND" if opacity < 0.999 else "OPAQUE",
                "doubleSided": True,
            }
        )
        mesh_index = len(meshes)
        meshes.append(
            {
                "primitives": [
                    {
                        "attributes": {"POSITION": pos_acc, "NORMAL": nrm_acc},
                        "indices": idx_acc,
                        "material": mat_index,
                        "mode": TRIANGLES,
                    }
                ]
            }
        )
        node_index = len(nodes)
        nodes.append(
            {
                "name": str(ent.get("name") or node_id)[:64],
                "mesh": mesh_index,
                "extras": {"nodeId": node_id},
            }
        )
        root_children.append(node_index)
        node_state[node_id] = {
            "visible": visible,
            "color": [float(color[0]), float(color[1]), float(color[2])],
            "opacity": opacity,
            "matrix": list(_LPS_TO_GLTF),
        }

    if not bin_data:
        bin_data.extend(b"\x00\x00\x00\x00")

    root_index = len(nodes)
    nodes.append(
        {
            "name": "HubMetaQuest",
            "children": root_children,
            "extras": {"generator": "HubInterface-image-display"},
        }
    )
    gltf = {
        "asset": {"version": "2.0", "generator": "HubInterface MetaQuest"},
        "scene": 0,
        "scenes": [{"nodes": [root_index]}],
        "nodes": nodes,
        "meshes": meshes,
        "materials": materials,
        "accessors": accessors,
        "bufferViews": buffer_views,
        "buffers": [{"byteLength": len(bin_data)}],
    }
    return _pack_glb(gltf, bytes(bin_data)), node_state


def collect_visible_mesh_entities(max_triangles_per_node: int = 200_000) -> List[Dict[str, Any]]:
    """Read visible model nodes from the current Slicer scene."""
    import slicer  # type: ignore
    import vtk

    entities: List[Dict[str, Any]] = []
    scene = slicer.mrmlScene
    models = scene.GetNodesByClass("vtkMRMLModelNode")
    models.UnRegister(None)
    for i in range(models.GetNumberOfItems()):
        node = models.GetItemAsObject(i)
        if node is None or node.GetHideFromEditors():
            continue
        display = node.GetDisplayNode()
        if display is None or not display.GetVisibility() or not display.GetVisibility3D():
            continue
        poly = node.GetPolyData()
        if poly is None:
            continue
        try:
            if poly.GetNumberOfCells() > max_triangles_per_node:
                dec = vtk.vtkQuadricDecimation()
                dec.SetInputData(poly)
                target = max(
                    0.05,
                    float(max_triangles_per_node) / max(1, poly.GetNumberOfCells()),
                )
                dec.SetTargetReduction(1.0 - min(1.0, target))
                dec.Update()
                poly = dec.GetOutput()
        except Exception as exc:
            LOGGER.debug("decimate skipped: %s", type(exc).__name__)
        arrays = _mesh_to_arrays(poly)
        if arrays is None:
            continue
        positions, normals, indices = arrays
        color = [0.8, 0.8, 0.8]
        try:
            color = list(display.GetColor())[:3]
        except Exception:
            pass
        opacity = 1.0
        try:
            opacity = float(display.GetOpacity())
        except Exception:
            pass
        entities.append(
            {
                "nodeId": node.GetID(),
                "name": node.GetName() or node.GetID(),
                "color": color,
                "opacity": opacity,
                "visible": True,
                "positions": positions,
                "normals": normals,
                "indices": indices,
            }
        )

    # Closed surface representations of segments (best-effort).
    try:
        segs = scene.GetNodesByClass("vtkMRMLSegmentationNode")
        segs.UnRegister(None)
        for i in range(segs.GetNumberOfItems()):
            seg_node = segs.GetItemAsObject(i)
            if seg_node is None:
                continue
            display = seg_node.GetDisplayNode()
            if display is None or not display.GetVisibility():
                continue
            segmentation = seg_node.GetSegmentation()
            if segmentation is None:
                continue
            for seg_idx in range(segmentation.GetNumberOfSegments()):
                segment = segmentation.GetNthSegment(seg_idx)
                if segment is None:
                    continue
                seg_id = segmentation.GetNthSegmentID(seg_idx)
                try:
                    if hasattr(display, "GetSegmentVisibility") and not display.GetSegmentVisibility(
                        seg_id
                    ):
                        continue
                except Exception:
                    pass
                poly = None
                try:
                    poly = seg_node.GetClosedSurfaceRepresentation(seg_id)
                except Exception:
                    poly = None
                if poly is None:
                    continue
                arrays = _mesh_to_arrays(poly)
                if arrays is None:
                    continue
                positions, normals, indices = arrays
                color = list(segment.GetColor())[:3] if hasattr(segment, "GetColor") else [0.2, 0.7, 0.3]
                opacity = 1.0
                try:
                    if hasattr(display, "GetSegmentOpacity3D"):
                        opacity = float(display.GetSegmentOpacity3D(seg_id))
                except Exception:
                    pass
                entities.append(
                    {
                        "nodeId": f"{seg_node.GetID()}/{seg_id}",
                        "name": segment.GetName() or seg_id,
                        "color": color,
                        "opacity": opacity,
                        "visible": True,
                        "positions": positions,
                        "normals": normals,
                        "indices": indices,
                    }
                )
    except Exception as exc:
        LOGGER.debug("segment export skipped: %s", type(exc).__name__)

    return entities


def build_glb_send_message(
    *,
    topic: str,
    snapshot_version: int,
    profile: str = "quest3",
) -> Tuple[Dict[str, Any], int, Dict[str, Any]]:
    """Collect scene meshes and build a multipart-ready ``glb-send`` message.

    Returns ``(message, entity_count, node_state)``.
    """
    entities = collect_visible_mesh_entities()
    glb_bytes, node_state = write_glb_from_entities(entities)
    digest = hashlib.sha256(glb_bytes).hexdigest()
    message = {
        "event": {
            "hub.topic": topic,
            "hub.event": "glb-send",
            "context": {
                "snapshotVersion": int(snapshot_version),
                "sha256": digest,
                "profile": profile,
                "nodeState": node_state,
                "files": [
                    {
                        "fileName": "scene.glb",
                        "mimeType": "model/gltf-binary",
                        "byteLength": len(glb_bytes),
                        "data": glb_bytes,
                    }
                ],
            },
        }
    }
    return message, len(entities), node_state


def build_meshscene_update_message(
    *,
    topic: str,
    scene_version: int,
    base_version: int,
    changes: List[Dict[str, Any]],
) -> Dict[str, Any]:
    return {
        "event": {
            "hub.topic": topic,
            "hub.event": "meshscene-update",
            "context": {
                "sceneVersion": int(scene_version),
                "baseVersion": int(base_version),
                "changes": changes,
            },
        }
    }

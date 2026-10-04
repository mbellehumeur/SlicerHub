"""Import Hub annotation-update payloads into Slicer markups (US pleura/B-line)."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Sequence, TYPE_CHECKING

import slicer
import vtk

if TYPE_CHECKING:
    from image_display_client_hub import ImageDisplayClientConnection

LOGGER = logging.getLogger("HubInterface.ImageDisplay")

US_TOOL_NAME = "UltrasoundPleuraBLineTool"
HUB_MARKUPS_FOLDER = "Hub/US-PleuraBline"
PLEURA_COLOR = (0.0, 1.0, 0.0)
BLINE_COLOR = (1.0, 0.0, 0.0)


def _context_dict(message: Dict[str, Any]) -> Dict[str, Any]:
    event = message.get("event") or {}
    context = event.get("context")
    return context if isinstance(context, dict) else {}


def _annotations_body(context: Dict[str, Any]) -> Dict[str, Any]:
    annotations = context.get("annotations")
    return annotations if isinstance(annotations, dict) else {}


def _find_volume_node(
    study_instance_uid: str,
    series_instance_uid: str,
) -> Optional[Any]:
    study_uid = (study_instance_uid or "").strip()
    series_uid = (series_instance_uid or "").strip()
    if not series_uid:
        return None

    for node in slicer.util.getNodesByClass("vtkMRMLScalarVolumeNode"):
        node_series = (node.GetAttribute("DICOM.seriesInstanceUID") or "").strip()
        if node_series != series_uid:
            continue
        if study_uid:
            node_study = (node.GetAttribute("DICOM.studyInstanceUID") or "").strip()
            if node_study and node_study != study_uid:
                continue
        return node
    return None


def _ijk_to_ras(matrix: vtk.vtkMatrix4x4, i: float, j: float, k: float) -> List[float]:
    point = [0.0, 0.0, 0.0, 1.0]
    matrix.MultiplyPoint([float(i), float(j), float(k), 1.0], point)
    return [point[0], point[1], point[2]]


def _remove_hub_markups() -> None:
    sh_node = slicer.vtkMRMLSubjectHierarchyNode.GetSubjectHierarchyNode(
        slicer.mrmlScene
    )
    folder_id = sh_node.GetItemByName(HUB_MARKUPS_FOLDER)
    if not folder_id:
        return
    child_ids = vtk.vtkIdList()
    sh_node.GetItemChildren(folder_id, child_ids, True)
    for index in range(child_ids.GetNumberOfIds()):
        item_id = child_ids.GetId(index)
        data_node = sh_node.GetItemDataNode(item_id)
        if data_node:
            slicer.mrmlScene.RemoveNode(data_node)


def _ensure_markups_folder() -> int:
    sh_node = slicer.vtkMRMLSubjectHierarchyNode.GetSubjectHierarchyNode(
        slicer.mrmlScene
    )
    folder_id = sh_node.GetItemByName(HUB_MARKUPS_FOLDER)
    if folder_id:
        return folder_id
    folder_id = sh_node.CreateFolderItem(sh_node.GetSceneItemID(), HUB_MARKUPS_FOLDER)
    return folder_id


def _add_line_node(
    folder_id: int,
    volume_node: Any,
    frame_index: int,
    segment: Sequence[Sequence[float]],
    line_kind: str,
    rater: str,
    line_index: int,
) -> None:
    if len(segment) != 2:
        return

    matrix = vtk.vtkMatrix4x4()
    volume_node.GetIJKToRASMatrix(matrix)

    ras_points: List[List[float]] = []
    for point in segment:
        if len(point) < 2:
            return
        col = float(point[0])
        row = float(point[1])
        ras_points.append(_ijk_to_ras(matrix, col, row, float(frame_index)))

    line_node = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLMarkupsLineNode")
    label = f"{line_kind}-f{frame_index + 1}-{line_index + 1}"
    if rater:
        label = f"{rater}/{label}"
    line_node.SetName(label)
    line_node.SetLocked(1)
    for ras in ras_points:
        line_node.AddControlPointWorld(ras)

    display_node = line_node.GetDisplayNode()
    if display_node:
        color = PLEURA_COLOR if line_kind == "pleura" else BLINE_COLOR
        display_node.SetSelectedColor(color)
        display_node.SetColor(color)

    sh_node = slicer.vtkMRMLSubjectHierarchyNode.GetSubjectHierarchyNode(
        slicer.mrmlScene
    )
    sh_node.SetItemParent(sh_node.GetItemByDataNode(line_node), folder_id)


def _import_us_annotations(context: Dict[str, Any]) -> bool:
    tool_name = str(context.get("toolName") or "").strip()
    if tool_name and tool_name != US_TOOL_NAME:
        LOGGER.info("annotation-update ignored: toolName=%s", tool_name)
        return False

    body = _annotations_body(context)
    frame_annotations = body.get("frame_annotations")
    if not isinstance(frame_annotations, dict) or not frame_annotations:
        LOGGER.warning("annotation-update ignored: missing frame_annotations")
        return False

    study_uid = str(context.get("studyInstanceUID") or "").strip()
    series_uid = str(context.get("seriesInstanceUID") or "").strip()
    volume_node = _find_volume_node(study_uid, series_uid)
    if volume_node is None:
        LOGGER.warning(
            "annotation-update queued: series %s not loaded in Slicer scene",
            series_uid or "(missing)",
        )
        return False

    _remove_hub_markups()
    folder_id = _ensure_markups_folder()
    rater = str(body.get("rater") or "").strip()
    imported = 0

    for frame_key, frame_data in frame_annotations.items():
        try:
            frame_index = int(frame_key)
        except (TypeError, ValueError):
            continue
        if frame_index < 0 or not isinstance(frame_data, dict):
            continue

        pleura_lines = frame_data.get("pleura_lines") or []
        b_lines = frame_data.get("b_lines") or []
        for line_index, segment in enumerate(pleura_lines):
            _add_line_node(
                folder_id,
                volume_node,
                frame_index,
                segment,
                "pleura",
                rater,
                line_index,
            )
            imported += 1
        for line_index, segment in enumerate(b_lines):
            _add_line_node(
                folder_id,
                volume_node,
                frame_index,
                segment,
                "bline",
                rater,
                line_index,
            )
            imported += 1

    if imported:
        slicer.util.forceRenderAllViews()
    LOGGER.info(
        "annotation-update applied %s line(s) for series %s",
        imported,
        series_uid,
    )
    return imported > 0


def handle_annotation_update(
    connection: "ImageDisplayClientConnection",
    message: Dict[str, Any],
) -> None:
    context = _context_dict(message)

    def run_import() -> None:
        try:
            _import_us_annotations(context)
        except Exception as exc:
            LOGGER.warning("annotation-update import failed: %s", exc)

    connection.schedule_main_thread(run_import, urgent=True)


def handle_annotation_delete(
    connection: "ImageDisplayClientConnection",
    message: Dict[str, Any],
) -> None:
    del message

    def run_delete() -> None:
        _remove_hub_markups()
        slicer.util.forceRenderAllViews()
        LOGGER.info("annotation-delete cleared Hub US markups")

    connection.schedule_main_thread(run_delete, urgent=True)

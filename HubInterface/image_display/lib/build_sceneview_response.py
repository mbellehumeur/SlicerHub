"""Build SCENEVIEW response payload from the live Slicer scene (main thread only)."""

from __future__ import annotations

import base64
import logging
from typing import Any, Dict, List, Optional, Sequence, Tuple

import qt

import slicer

from imaging_study_context import extract_dicom_series_uid, extract_dicom_study_uid

LOGGER = logging.getLogger("HubInterface.ImageDisplay")

# Bump when debugging sceneview layout/window coordinates.
SCENEVIEW_BUILD_ID = "2026-06-11-frame-left-top"

HUB_VIEW_THUMBNAIL_MAX_WIDTH = 160

LOGGER.info("build_sceneview_response loaded buildId=%s", SCENEVIEW_BUILD_ID)


def _maybe_call(value: Any) -> Any:
    """Call ``value`` when it is a zero-arg callable; otherwise return as-is."""
    if not callable(value):
        return value
    try:
        return value()
    except TypeError:
        return value


def _qt_scalar(obj: Any, name: str, default: int = 0) -> int:
    """Read a Qt int getter exposed as either a method or PythonQt property."""
    value = _maybe_call(getattr(obj, name, default))
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _qt_member(obj: Any, name: str, default: Any = None) -> Any:
    """Read a Qt member exposed as either a zero-arg method or PythonQt property."""
    return _maybe_call(getattr(obj, name, default))


def _qt_is_null(obj: Any) -> bool:
    """Return True when a Qt object reports isNull (method or bool property)."""
    return bool(_qt_member(obj, "isNull", False))


def _string_list(value: Any) -> List[str]:
    value = _maybe_call(value)
    if value is None:
        return []
    try:
        return [str(item) for item in value]
    except TypeError:
        return [str(value)] if str(value) else []


def _resolve_render_widget(widget: qt.QWidget) -> qt.QWidget:
    for attr in ("sliceView", "threeDView"):
        member = getattr(widget, attr, None)
        if member is None:
            continue
        if callable(member):
            try:
                resolved = member()
                if resolved is not None:
                    return resolved
            except TypeError:
                continue
        elif hasattr(member, "grab"):
            return member
    return widget


def _slice_orientation_label(logic: Any) -> str:
    for attr in ("GetSliceOrientationString", "GetSliceOrientation"):
        member = getattr(logic, attr, None)
        if member is None:
            continue
        if callable(member):
            try:
                return str(member())
            except TypeError:
                continue
        return str(member)
    return ""


def _layout_widget(
    layout_manager: Any, getter_name: str, view_name: str
) -> Optional[qt.QWidget]:
    getter = getattr(layout_manager, getter_name, None)
    if not callable(getter):
        LOGGER.debug(
            "layout manager %s is not callable for view %s",
            getter_name,
            view_name,
        )
        return None
    try:
        return getter(view_name)
    except TypeError as exc:
        LOGGER.debug(
            "layout manager %s failed for view %s: %s",
            getter_name,
            view_name,
            exc,
        )
        return None


def _screen_rect_from_widget(
    widget: qt.QWidget, origin_widget: Optional[qt.QWidget] = None
) -> Tuple[Optional[Dict[str, int]], Optional[Dict[str, int]]]:
    if widget is None:
        return None, None
    try:
        global_pos = widget.mapToGlobal(qt.QPoint(0, 0))
        width = _qt_scalar(widget, "width")
        height = _qt_scalar(widget, "height")
        screen_rect = {
            "left": _qt_scalar(global_pos, "x"),
            "top": _qt_scalar(global_pos, "y"),
            "width": width,
            "height": height,
        }
        layout_rect: Optional[Dict[str, int]] = None
        if origin_widget is not None:
            local = widget.mapTo(origin_widget, qt.QPoint(0, 0))
            layout_rect = {
                "left": _qt_scalar(local, "x"),
                "top": _qt_scalar(local, "y"),
                "width": width,
                "height": height,
            }
        return screen_rect, layout_rect
    except Exception as exc:
        LOGGER.debug("screen rect failed: %s", exc)
        return None, None


def _encode_qimage_png(image: qt.QImage) -> Optional[Dict[str, Any]]:
    if _qt_is_null(image):
        return None
    ba = qt.QByteArray()
    buf = qt.QBuffer(ba)
    buf.open(qt.QIODevice.WriteOnly)
    if not image.save(buf, "PNG"):
        return None
    data = base64.b64encode(bytes(ba.data())).decode("ascii")
    if not data:
        return None
    return {
        "contentType": "image/png",
        "data": data,
        "width": _qt_scalar(image, "width"),
        "height": _qt_scalar(image, "height"),
    }


def _scale_thumbnail_png(
    pixmap: qt.QPixmap, max_width: int = HUB_VIEW_THUMBNAIL_MAX_WIDTH
) -> Optional[Dict[str, Any]]:
    if _qt_is_null(pixmap):
        return None
    image = _qt_member(pixmap, "toImage")
    if image is None or _qt_is_null(image):
        return None
    width = _qt_scalar(image, "width")
    height = _qt_scalar(image, "height")
    if width <= 0 or height <= 0:
        return None
    scale = min(1.0, max_width / width)
    out_w = max(1, round(width * scale))
    out_h = max(1, round(height * scale))
    scaled = image.scaled(
        out_w,
        out_h,
        qt.Qt.KeepAspectRatio,
        qt.Qt.SmoothTransformation,
    )
    return _encode_qimage_png(scaled)


def _placeholder_thumbnail_png(
    label: str, max_width: int = HUB_VIEW_THUMBNAIL_MAX_WIDTH
) -> Dict[str, Any]:
    src_w = max(1, int(max_width))
    src_h = max(1, round(src_w * 0.62))
    image = qt.QImage(src_w, src_h, qt.QImage.Format_RGB32)
    image.fill(qt.QColor("#0a0a12"))
    painter = qt.QPainter(image)
    painter.setPen(qt.QColor("#ffc107"))
    painter.drawRect(1, 1, src_w - 2, src_h - 2)
    painter.setPen(qt.QColor("#ffffff"))
    font = qt.QFont()
    font.setBold(True)
    font.setPointSize(max(8, round(10 * src_w / 160)))
    painter.setFont(font)
    text = (label or "Viewport").strip() or "Viewport"
    painter.drawText(qt.QRect(0, 0, src_w, src_h), qt.Qt.AlignCenter, text)
    painter.end()
    encoded = _encode_qimage_png(image)
    if encoded is None:
        raise RuntimeError("placeholder thumbnail PNG encode failed")
    return encoded


def capture_viewport_thumbnail_png(
    widget: Optional[qt.QWidget],
    label: str,
    max_width: int = HUB_VIEW_THUMBNAIL_MAX_WIDTH,
) -> Dict[str, Any]:
    """Return a mandatory PNG thumbnail dict for one viewport."""
    slicer.app.processEvents()
    thumb: Optional[Dict[str, Any]] = None
    if widget is not None:
        try:
            render_widget = _resolve_render_widget(widget)
            pixmap = _qt_member(render_widget, "grab")
            if pixmap is None or _qt_is_null(pixmap):
                raise RuntimeError("grab returned no pixmap")
            thumb = _scale_thumbnail_png(pixmap, max_width)
        except Exception as exc:
            LOGGER.warning(
                "viewport grab failed for %s: %s",
                label,
                exc,
            )
    if thumb is None or not str(thumb.get("data") or "").strip():
        LOGGER.warning("using placeholder thumbnail for %s", label)
        thumb = _placeholder_thumbnail_png(label, max_width)
    return thumb


def _frame_geometry_rect(main_window: qt.QWidget) -> Any:
    """Outer window frame in global screen coordinates (prefer QWindow handle)."""
    window_handle = _maybe_call(getattr(main_window, "windowHandle", None))
    if window_handle is not None:
        frame = _qt_member(window_handle, "frameGeometry")
        if frame is not None:
            return frame
    return _qt_member(main_window, "frameGeometry")


def _window_payload(main_window: qt.QWidget) -> Dict[str, int]:
    """Browser-compatible window metrics plus native ``frameLeft``/``frameTop``.

    ``screenX``/``screenY`` remain the client-area origin (Chromium semantics).
    ``frameLeft``/``frameTop`` are the true outer frame top-left from Qt so the
    worklist diagram can place native windows without inferring chrome borders.
    """
    client_origin = main_window.mapToGlobal(qt.QPoint(0, 0))
    frame = _frame_geometry_rect(main_window)
    if frame is None:
        frame = qt.QRect(0, 0, 0, 0)
    return {
        "screenX": _qt_scalar(client_origin, "x"),
        "screenY": _qt_scalar(client_origin, "y"),
        "frameLeft": _qt_scalar(frame, "x"),
        "frameTop": _qt_scalar(frame, "y"),
        "outerWidth": _qt_scalar(frame, "width"),
        "outerHeight": _qt_scalar(frame, "height"),
        "innerWidth": _qt_scalar(main_window, "width"),
        "innerHeight": _qt_scalar(main_window, "height"),
    }


def _layout_origin_widget(main_window: qt.QWidget) -> qt.QWidget:
    central = _qt_member(main_window, "centralWidget")
    return central if central is not None else main_window


def _layout_grid_widget(layout_manager: Any, main_window: qt.QWidget) -> qt.QWidget:
    """Viewport grid host (ctkLayoutManager viewport), not full centralWidget."""
    viewport = _maybe_call(getattr(layout_manager, "viewport", None))
    if viewport is not None and isinstance(viewport, qt.QWidget):
        return viewport
    view_widgets = _maybe_call(getattr(layout_manager, "viewWidgets", None))
    if view_widgets is not None:
        try:
            widgets = list(view_widgets)
        except TypeError:
            widgets = []
        for widget in widgets:
            if widget is None or not isinstance(widget, qt.QWidget):
                continue
            parent = widget.parentWidget()
            if parent is not None:
                return parent
    return _layout_origin_widget(main_window)


def _viewport_thumbnail(
    widget: Optional[qt.QWidget],
    label: str,
    *,
    fast_placeholder_thumbnails: bool,
) -> Dict[str, Any]:
    if fast_placeholder_thumbnails:
        return _placeholder_thumbnail_png(label)
    return capture_viewport_thumbnail_png(widget, label)


def _collect_slice_viewports(
    layout_manager: Any,
    origin_widget: qt.QWidget,
    open_context: Sequence[Any],
    slot_index_start: int,
    *,
    fast_placeholder_thumbnails: bool,
) -> Tuple[List[Dict[str, Any]], int]:
    viewports: List[Dict[str, Any]] = []
    slot_index = slot_index_start
    study_uid = extract_dicom_study_uid(open_context)
    series_uid = extract_dicom_series_uid(open_context)

    slice_names = _string_list(getattr(layout_manager, "sliceViewNames", None))
    for name_str in slice_names:
        widget = _layout_widget(layout_manager, "sliceWidget", name_str)
        if widget is None:
            continue
        bounds_widget = _resolve_render_widget(widget)
        screen_rect, layout_rect = _screen_rect_from_widget(
            bounds_widget, origin_widget
        )
        viewport: Dict[str, Any] = {
            "viewId": name_str,
            "type": "2D",
            "slotIndex": slot_index,
            "name": name_str,
            "screenRect": screen_rect,
            "layoutRect": layout_rect,
            "thumbnail": _viewport_thumbnail(
                widget,
                name_str,
                fast_placeholder_thumbnails=fast_placeholder_thumbnails,
            ),
        }
        if study_uid:
            viewport["studyInstanceUID"] = study_uid
        if series_uid:
            viewport["seriesInstanceUID"] = series_uid
        slice_logic = _maybe_call(getattr(widget, "sliceLogic", None))
        if slice_logic is not None:
            orientation = _slice_orientation_label(slice_logic)
            if orientation:
                viewport["orientation"] = orientation
        viewports.append(viewport)
        slot_index += 1
    return viewports, slot_index


def _collect_three_d_viewports(
    layout_manager: Any,
    origin_widget: qt.QWidget,
    open_context: Sequence[Any],
    slot_index_start: int,
    *,
    fast_placeholder_thumbnails: bool,
) -> Tuple[List[Dict[str, Any]], int]:
    viewports: List[Dict[str, Any]] = []
    slot_index = slot_index_start
    study_uid = extract_dicom_study_uid(open_context)
    series_uid = extract_dicom_series_uid(open_context)

    three_d_names = _string_list(getattr(layout_manager, "threeDViewNames", None))
    for name_str in three_d_names:
        widget = _layout_widget(layout_manager, "threeDWidget", name_str)
        if widget is None:
            continue
        bounds_widget = _resolve_render_widget(widget)
        screen_rect, layout_rect = _screen_rect_from_widget(
            bounds_widget, origin_widget
        )
        viewport: Dict[str, Any] = {
            "viewId": name_str,
            "type": "3D",
            "slotIndex": slot_index,
            "name": name_str,
            "screenRect": screen_rect,
            "layoutRect": layout_rect,
            "thumbnail": _viewport_thumbnail(
                widget,
                name_str,
                fast_placeholder_thumbnails=fast_placeholder_thumbnails,
            ),
        }
        if study_uid:
            viewport["studyInstanceUID"] = study_uid
        if series_uid:
            viewport["seriesInstanceUID"] = series_uid
        viewports.append(viewport)
        slot_index += 1
    return viewports, slot_index


def _validate_viewports(viewports: List[Dict[str, Any]]) -> None:
    for index, viewport in enumerate(viewports):
        thumb = viewport.get("thumbnail")
        if not isinstance(thumb, dict):
            raise RuntimeError(f"viewport {index} missing thumbnail")
        if thumb.get("contentType") != "image/png":
            raise RuntimeError(f"viewport {index} thumbnail must be image/png")
        if not str(thumb.get("data") or "").strip():
            raise RuntimeError(f"viewport {index} thumbnail data is empty")


def build_sceneview_response_payload(
    product_name: str,
    open_context: Optional[Sequence[Any]] = None,
    *,
    fast_placeholder_thumbnails: bool = False,
) -> Dict[str, Any]:
    """Build sceneview-response ``data`` from the current Slicer UI."""
    LOGGER.info(
        "build_sceneview_response_payload buildId=%s fastPlaceholder=%s",
        SCENEVIEW_BUILD_ID,
        fast_placeholder_thumbnails,
    )
    context = list(open_context or [])
    main_window = slicer.util.mainWindow()
    layout_manager = slicer.app.layoutManager()
    origin_widget = _layout_grid_widget(layout_manager, main_window)
    LOGGER.debug(
        "sceneview layout grid widget class=%s size=%sx%s",
        type(origin_widget).__name__,
        _qt_scalar(origin_widget, "width"),
        _qt_scalar(origin_widget, "height"),
    )

    layout_screen_rect, _ = _screen_rect_from_widget(origin_widget)
    layout_client_size = {
        "width": _qt_scalar(origin_widget, "width"),
        "height": _qt_scalar(origin_widget, "height"),
    }

    active_view_id: Optional[str] = None
    active_view = _maybe_call(getattr(layout_manager, "activeView", None))
    if active_view is not None and str(active_view).strip():
        active_view_id = str(active_view)

    layout_name: Optional[str] = None
    layout_description = _maybe_call(
        getattr(layout_manager, "layoutDescription", None)
    )
    if layout_description is not None:
        desc = str(layout_description).strip()
        if desc:
            layout_name = desc

    viewports: List[Dict[str, Any]] = []
    slot_index = 0
    slice_viewports, slot_index = _collect_slice_viewports(
        layout_manager,
        origin_widget,
        context,
        slot_index,
        fast_placeholder_thumbnails=fast_placeholder_thumbnails,
    )
    viewports.extend(slice_viewports)
    three_d_viewports, _slot_index = _collect_three_d_viewports(
        layout_manager,
        origin_widget,
        context,
        slot_index,
        fast_placeholder_thumbnails=fast_placeholder_thumbnails,
    )
    viewports.extend(three_d_viewports)

    if not viewports:
        placeholder = _viewport_thumbnail(
            main_window,
            product_name or "3D Slicer",
            fast_placeholder_thumbnails=True,
        )
        viewports.append(
            {
                "viewId": "main",
                "type": "3D",
                "slotIndex": 0,
                "name": "Main",
                "screenRect": layout_screen_rect,
                "layoutRect": {
                    "left": 0,
                    "top": 0,
                    "width": layout_client_size["width"],
                    "height": layout_client_size["height"],
                },
                "thumbnail": placeholder,
            }
        )

    _validate_viewports(viewports)

    payload: Dict[str, Any] = {
        "source": "sceneview",
        "product": (product_name or "").strip() or "3DSLICER-ID",
        "window": _window_payload(main_window),
        "display": {
            "layoutName": layout_name,
            "activeViewId": active_view_id,
            "layoutScreenRect": layout_screen_rect,
            "layoutClientSize": layout_client_size,
        },
        "viewports": viewports,
    }
    LOGGER.info(
        "built sceneview payload buildId=%s product=%s viewportCount=%d",
        SCENEVIEW_BUILD_ID,
        payload["product"],
        len(viewports),
    )
    return payload

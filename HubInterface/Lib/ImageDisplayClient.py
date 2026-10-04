"""Slicer Hub — Image Display Client subsection."""

from __future__ import annotations

import logging
import queue
from collections import deque
from typing import Any, Callable, Dict, Optional

import qt

import slicer
from slicer.i18n import tr as _

from hub_client import generate_subscriber_name
from .conference import hub_endpoint_for_name
from .ConferenceDialog import ConferenceDialog
from .ResourceServers import (
    DEFAULT_HUB_NAME,
    HUBS,
    hub_admin_url_for_name,
    MAIN_QUEUE_TIMER_MS,
    normalize_hub_name,
)
from image_display_client_hub import (
    DISPLAY_PRODUCT_NAME,
    DISPLAY_PRODUCT_VERSION,
    ImageDisplayClientConnection,
    disconnect_image_display_client,
)

LOGGER = logging.getLogger("HubInterface.ImageDisplay")
LOGGER.setLevel(logging.INFO)

DEFAULT_DISPLAY_TOPIC = "USER-1"

_SETTINGS_GROUP = "HubInterface"
_SETTINGS_KEY_HUB = "imageDisplayHub"
_SETTINGS_KEY_TOPIC = "imageDisplayTopic"

_STATUS_TEXT_STYLE_IDLE = "color: palette(text);"
_STATUS_TEXT_STYLE_CONNECTED = "color: #2e7d32; font-weight: bold;"
_STATUS_TEXT_STYLE_ACTIVE = "color: #1a5f9e; font-weight: bold;"
_STATUS_TEXT_STYLE_ERROR = "color: #c45c26; font-weight: bold;"


def _connected_status_text(detail: Dict[str, Any]) -> str:
    subscriber = str(detail.get("subscriber_name") or "").strip()
    last_event = str(detail.get("last_event") or "").strip()
    study_id = str(detail.get("open_study_id") or "").strip()
    load_status = str(detail.get("load_status") or "").strip()
    load_error = str(detail.get("load_error") or "").strip()

    if subscriber:
        text = _("Connected as {name}").format(name=subscriber)
    else:
        text = _("Connected")

    if load_status == "loaded" and study_id:
        text = _("{base} — {study} loaded").format(base=text, study=study_id)
    elif load_status == "error":
        if load_error:
            text = _("{base} — load failed: {reason}").format(
                base=text,
                reason=load_error,
            )
        else:
            text = _("{base} — load failed").format(base=text)
    elif last_event:
        if study_id and last_event == "imagingstudy-open":
            text = _("{base} — loading {study}…").format(
                base=text,
                study=study_id,
            )
        else:
            text = _("{base} — last: {event}").format(base=text, event=last_event)

    conference_title = str(detail.get("conference_title") or "").strip()
    if conference_title:
        text = _("{base} — Conference: {title}").format(
            base=text,
            title=conference_title,
        )

    return text


def _connected_status_tooltip(detail: Dict[str, Any]) -> str:
    lines = []
    subscriber = str(detail.get("subscriber_name") or "").strip()
    if subscriber:
        lines.append(_("Subscriber: {name}").format(name=subscriber))
    message_count = int(detail.get("message_count") or 0)
    if message_count == 1:
        lines.append(_("Messages received: 1"))
    elif message_count > 1:
        lines.append(
            _("Messages received: {count}").format(count=message_count)
        )
    open_mode = str(detail.get("open_mode") or "").strip()
    if open_mode:
        lines.append(_("Open mode: {mode}").format(mode=open_mode))
    load_status = str(detail.get("load_status") or "").strip()
    if load_status:
        lines.append(_("Load status: {status}").format(status=load_status))
    load_error = str(detail.get("load_error") or "").strip()
    if load_error:
        lines.append(_("Load error: {reason}").format(reason=load_error))
    conference_title = str(detail.get("conference_title") or "").strip()
    if conference_title:
        lines.append(_("Conference: {title}").format(title=conference_title))
    participants = detail.get("conference_participants") or []
    if isinstance(participants, list) and participants:
        lines.append(
            _("Conference participants: {names}").format(
                names=", ".join(str(name) for name in participants)
            )
        )
    return "\n".join(lines)


class ImageDisplayClientWidget:
    """UI and hub connection for the Image Display Client section."""

    def __init__(self) -> None:
        self._section: Optional[qt.QWidget] = None
        self._hub = ImageDisplayClientConnection(self.post_ui, self.post_ui_urgent)
        self._main_queue: queue.Queue[Callable[[], None]] = queue.Queue()
        self._main_queue_urgent: deque[Callable[[], None]] = deque()
        self._main_queue_running = False
        self._setup_complete = False
        self._main_queue_timer = qt.QTimer()
        self._main_queue_timer.setInterval(MAIN_QUEUE_TIMER_MS)
        self._main_queue_timer.timeout.connect(self._main_queue_process)

        self.hubComboBox: Optional[qt.QComboBox] = None
        self.openHubButton: Optional[qt.QPushButton] = None
        self.topicEdit: Optional[qt.QLineEdit] = None
        self.oidcCheckBox: Optional[qt.QCheckBox] = None
        self.connectButton: Optional[qt.QPushButton] = None
        self.disconnectButton: Optional[qt.QPushButton] = None
        self.conferenceButton: Optional[qt.QPushButton] = None
        self.statusLabel: Optional[qt.QLabel] = None
        self._conference_dialog: Optional[ConferenceDialog] = None

    def setup(self, section: qt.QWidget) -> None:
        if self._setup_complete:
            return
        self._setup_complete = True
        self._section = section
        layout = qt.QFormLayout(section)
        layout.setLabelAlignment(qt.Qt.AlignLeft)
        layout.setHorizontalSpacing(84)

        self.conferenceButton = qt.QPushButton(_("Start a conference"))
        self.conferenceButton.setToolTip(_("Create or manage a Hub conference"))
        self.conferenceButton.clicked.connect(self._on_conference)
        self.conferenceButton.enabled = False
        self.conferenceButton.setSizePolicy(
            qt.QSizePolicy.Fixed, qt.QSizePolicy.Fixed
        )
        conference_row = qt.QWidget()
        conference_row_layout = qt.QHBoxLayout(conference_row)
        conference_row_layout.setContentsMargins(0, 0, 0, 0)
        conference_row_layout.addWidget(self.conferenceButton, 0)
        conference_row_layout.addStretch(1)
        layout.addRow(_("Conferencing:"), conference_row)

        hub_row = qt.QWidget()
        hub_row_layout = qt.QHBoxLayout(hub_row)
        hub_row_layout.setContentsMargins(0, 0, 0, 0)
        self.hubComboBox = qt.QComboBox()
        for hub_name in sorted(HUBS.keys()):
            self.hubComboBox.addItem(hub_name)
        saved_hub = normalize_hub_name(
            self._load_setting(_SETTINGS_KEY_HUB, DEFAULT_HUB_NAME)
        )
        hub_index = self.hubComboBox.findText(saved_hub)
        if hub_index >= 0:
            self.hubComboBox.setCurrentIndex(hub_index)
        self.openHubButton = qt.QPushButton(_("Open"))
        self.openHubButton.setSizePolicy(
            qt.QSizePolicy.Fixed, qt.QSizePolicy.Fixed
        )
        self.openHubButton.setToolTip(
            _("Open the hub admin portal in your default browser")
        )
        self.openHubButton.clicked.connect(self._on_open_hub)
        self.connectButton = qt.QPushButton(_("Connect"))
        self.connectButton.clicked.connect(self._on_connect)
        self.disconnectButton = qt.QPushButton(_("Disconnect"))
        self.disconnectButton.clicked.connect(self._on_disconnect)
        self.disconnectButton.enabled = False
        metrics = qt.QFontMetrics(self.hubComboBox.font)
        max_hub_text_w = max(
            metrics.horizontalAdvance(name) for name in HUBS.keys()
        )
        hub_combo_pad = 48
        self.hubComboBox.setMaximumWidth(max_hub_text_w + hub_combo_pad)
        hub_row_layout.addWidget(self.connectButton, 0)
        hub_row_layout.addWidget(self.disconnectButton, 0)
        hub_row_layout.addWidget(self.hubComboBox, 0)
        hub_row_layout.addWidget(self.openHubButton, 0)
        hub_row_layout.addStretch(1)
        hub_row.setSizePolicy(qt.QSizePolicy.Expanding, qt.QSizePolicy.Fixed)
        layout.addRow(_("Hub:"), hub_row)

        topic_row = qt.QWidget()
        topic_row_layout = qt.QHBoxLayout(topic_row)
        topic_row_layout.setContentsMargins(0, 0, 0, 0)
        self.topicEdit = qt.QLineEdit(
            self._load_setting(_SETTINGS_KEY_TOPIC, DEFAULT_DISPLAY_TOPIC)
        )
        self.topicEdit.setPlaceholderText(_("Hub topic"))
        self.oidcCheckBox = qt.QCheckBox()
        oidc_label = qt.QLabel(_("OIDC:"))
        topic_row_layout.addWidget(self.topicEdit, 1)
        topic_row_layout.addWidget(oidc_label, 0)
        topic_row_layout.addWidget(self.oidcCheckBox, 0)
        layout.addRow(_("User:"), topic_row)

        self.statusLabel = qt.QLabel(_("Disconnected"))
        self._apply_status_style("idle")
        status_row = qt.QWidget()
        status_row_layout = qt.QHBoxLayout(status_row)
        status_row_layout.setContentsMargins(0, 0, 0, 0)
        status_heading = qt.QLabel(_("Status:"))
        status_row_layout.addWidget(status_heading)
        status_row_layout.addWidget(self.statusLabel, 1)
        layout.addRow(status_row)

    @staticmethod
    def _load_setting(key: str, default: str) -> str:
        settings = qt.QSettings()
        settings.beginGroup(_SETTINGS_GROUP)
        value = settings.value(key, default)
        settings.endGroup()
        return str(value).strip() if value else default

    def _save_settings(self) -> None:
        settings = qt.QSettings()
        settings.beginGroup(_SETTINGS_GROUP)
        if self.hubComboBox:
            settings.setValue(_SETTINGS_KEY_HUB, self.hubComboBox.currentText)
        if self.topicEdit:
            settings.setValue(_SETTINGS_KEY_TOPIC, self.topicEdit.text.strip())
        settings.endGroup()

    @staticmethod
    def _product_name() -> str:
        return DISPLAY_PRODUCT_NAME

    @staticmethod
    def _product_version() -> str:
        return DISPLAY_PRODUCT_VERSION

    def cleanup(self) -> None:
        self.exit()
        disconnect_image_display_client()
        self._main_queue_drain()

    def enter(self) -> None:
        self._main_queue_running = True
        self._main_queue_timer.start()
        self._main_queue_drain()
        self._refresh_status()

    def exit(self) -> None:
        self._main_queue_running = False
        self._main_queue_timer.stop()

    def post_ui(self, fn: Callable[[], None]) -> None:
        self._main_queue.put(fn)

    def post_ui_urgent(self, fn: Callable[[], None]) -> None:
        self._main_queue_urgent.append(fn)

    def _main_queue_drain(self) -> None:
        try:
            while self._main_queue_urgent:
                fn = self._main_queue_urgent.popleft()
                fn()
            while not self._main_queue.empty():
                fn = self._main_queue.get_nowait()
                fn()
        except Exception as exc:
            LOGGER.exception("Hub Image Display main queue error: %s", exc)

    def _main_queue_process(self) -> None:
        if not self._main_queue_running and not self._hub.isHubThreadRunning():
            return
        self._main_queue_drain()

    def _apply_status_style(self, variant: str) -> None:
        if not self.statusLabel:
            return
        styles = {
            "idle": _STATUS_TEXT_STYLE_IDLE,
            "connected": _STATUS_TEXT_STYLE_CONNECTED,
            "active": _STATUS_TEXT_STYLE_ACTIVE,
            "error": _STATUS_TEXT_STYLE_ERROR,
            "failed": _STATUS_TEXT_STYLE_ERROR,
        }
        self.statusLabel.setStyleSheet(
            styles.get(variant, _STATUS_TEXT_STYLE_IDLE)
        )

    def _set_hub_session_buttons_enabled(self, enabled: bool) -> None:
        if self.disconnectButton:
            self.disconnectButton.enabled = enabled
        if self.conferenceButton:
            self.conferenceButton.enabled = enabled

    def _set_fields_enabled(self, enabled: bool) -> None:
        if self.hubComboBox:
            self.hubComboBox.setEnabled(enabled)
        if self.openHubButton:
            self.openHubButton.setEnabled(True)
        if self.topicEdit:
            self.topicEdit.setEnabled(enabled)

    def _on_open_hub(self) -> None:
        if not self.hubComboBox:
            return
        hub_name = self.hubComboBox.currentText
        admin_url = hub_admin_url_for_name(hub_name)
        if not admin_url:
            slicer.util.warningDisplay(
                _("No admin URL for hub {name}").format(name=hub_name)
            )
            return
        if not qt.QDesktopServices.openUrl(qt.QUrl(admin_url)):
            slicer.util.warningDisplay(
                _("Could not open hub admin URL:\n{url}").format(url=admin_url)
            )

    def _on_connect(self) -> None:
        if not self.topicEdit or not self.hubComboBox:
            return
        topic = self.topicEdit.text.strip()
        if not topic:
            slicer.util.errorDisplay(_("Enter a topic before connecting."))
            return

        product_name = self._product_name()
        product_version = self._product_version()
        subscriber = generate_subscriber_name(product_name)

        self._save_settings()

        if self._hub.isHubThreadRunning():
            return

        self._set_fields_enabled(False)
        self._apply_status_style("active")
        if self.statusLabel:
            self.statusLabel.text = _("Connecting…")
        if self.connectButton:
            self.connectButton.enabled = False
        self._set_hub_session_buttons_enabled(True)

        self._ensure_main_queue_active()
        self._hub.connectHub(
            self.hubComboBox.currentText,
            topic,
            subscriber,
            product_name,
            product_version,
            status_callback=self._on_hub_connection_state,
        )

    def _ensure_main_queue_active(self) -> None:
        self._main_queue_running = True
        if not self._main_queue_timer.isActive():
            self._main_queue_timer.start()

    def _on_disconnect(self) -> None:
        from image_display_client_load import clear_loaded_study

        clear_loaded_study(
            self._hub.get_loaded_node_ids(),
            self._hub.get_temp_dir(),
        )
        self._hub.clear_load_state()
        self._hub.disconnectHub()
        self._refresh_status()

    def _on_conference(self) -> None:
        if not self._hub.isHubConnected():
            slicer.util.warningDisplay(_("Connect to the Slicer hub first."))
            return
        if not self.hubComboBox or not self.topicEdit:
            return

        hub_name = self.hubComboBox.currentText
        hub_endpoint = hub_endpoint_for_name(hub_name)
        if not hub_endpoint:
            slicer.util.warningDisplay(
                _("No hub endpoint for {name}").format(name=hub_name)
            )
            return

        if self._conference_dialog is None:
            parent = self._section if self._section else None
            self._conference_dialog = ConferenceDialog(parent)

        self._conference_dialog.configure(
            hub_endpoint=hub_endpoint,
            session_topic=self.topicEdit.text.strip(),
            subscriber_name=self._hub.get_subscriber_name(),
            connected=self._hub.isHubConnected(),
        )
        self._conference_dialog.show()
        self._conference_dialog.raise_()
        self._conference_dialog.activateWindow()

    def _on_hub_connection_state(
        self, state: str, detail: Optional[Dict[str, Any]] = None
    ) -> None:
        detail = detail or {}

        if state == "connected":
            load_status = str(detail.get("load_status") or "").strip()
            self._apply_status_style(
                "error" if load_status == "error" else "connected"
            )
            status_detail = self._hub.get_status_detail()
            status_detail.update(
                {
                    k: v
                    for k, v in detail.items()
                    if k not in status_detail or detail.get(k)
                }
            )
            if self.statusLabel:
                self.statusLabel.text = _connected_status_text(status_detail)
                self.statusLabel.toolTip = _connected_status_tooltip(status_detail)
            if self.connectButton:
                self.connectButton.enabled = False
            self._set_hub_session_buttons_enabled(True)
            self._set_fields_enabled(False)
        elif state == "connecting":
            self._apply_status_style("active")
            if self.statusLabel:
                self.statusLabel.text = _("Connecting…")
            if self.connectButton:
                self.connectButton.enabled = False
            self._set_hub_session_buttons_enabled(True)
            self._set_fields_enabled(False)
        elif state == "reconnecting":
            self._apply_status_style("active")
            if self.statusLabel:
                self.statusLabel.text = _("Reconnecting…")
            if self.connectButton:
                self.connectButton.enabled = False
            self._set_hub_session_buttons_enabled(True)
            self._set_fields_enabled(False)
        elif state == "failed":
            self._apply_status_style("failed")
            reason = detail.get("reason") or _("Unknown error")
            if self.statusLabel:
                self.statusLabel.text = _("Cannot connect: {reason}").format(
                    reason=reason
                )
            if self.connectButton:
                self.connectButton.enabled = True
            self._set_hub_session_buttons_enabled(False)
            self._set_fields_enabled(True)
        elif state == "disconnected":
            self._apply_status_style("idle")
            if self.statusLabel:
                self.statusLabel.text = _("Disconnected")
                self.statusLabel.toolTip = ""
            if self.connectButton:
                self.connectButton.enabled = True
            self._set_hub_session_buttons_enabled(False)
            self._set_fields_enabled(True)
        elif state == "error":
            self._apply_status_style("error")
            reason = detail.get("reason")
            if self.statusLabel:
                if reason:
                    self.statusLabel.text = _("Connection error: {reason}").format(
                        reason=reason
                    )
                else:
                    self.statusLabel.text = _("Connection error (reconnecting)")
            if self.connectButton:
                self.connectButton.enabled = False
            self._set_hub_session_buttons_enabled(True)
            self._set_fields_enabled(False)

    def _refresh_status(self) -> None:
        if self._hub.isHubConnected():
            self._apply_status_style("connected")
            if self.statusLabel:
                status_detail = self._hub.get_status_detail()
                load_status = status_detail.get("load_status") or ""
                self._apply_status_style(
                    "error" if load_status == "error" else "connected"
                )
                self.statusLabel.text = _connected_status_text(status_detail)
                self.statusLabel.toolTip = _connected_status_tooltip(status_detail)
            if self.connectButton:
                self.connectButton.enabled = False
            self._set_hub_session_buttons_enabled(True)
            self._set_fields_enabled(False)
        elif not self._hub.isHubThreadRunning():
            self._apply_status_style("idle")
            if self.statusLabel:
                self.statusLabel.text = _("Disconnected")
                self.statusLabel.toolTip = ""
            if self.connectButton:
                self.connectButton.enabled = True
            self._set_hub_session_buttons_enabled(False)
            self._set_fields_enabled(True)

"""Native Qt dialog for Slicer hub conferencing."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import qt

from slicer.i18n import tr as _

from .conference import (
    HUB_CONFERENCE_EXIT_ACK_MS,
    HUB_CONFERENCE_TITLE_PRESETS,
    conference_host_topic,
    create_cast_conference,
    delete_cast_conference,
    fetch_conference_topics,
    fetch_conferences,
    find_active_cast_conference,
    is_cast_conference_host,
)

LOGGER = logging.getLogger("HubInterface.ConferenceDialog")

_STATUS_STYLE_SUCCESS = "color: #2e7d32;"
_STATUS_STYLE_ERROR = "color: #c45c26;"


class _ConferenceFetchWorker(qt.QThread):
    """Background fetch for conference topics and active conferences."""

    finished_with_data = qt.Signal(object, object, object)

    def __init__(
        self,
        hub_endpoint: str,
        parent: Optional[qt.QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._hub_endpoint = hub_endpoint

    def run(self) -> None:
        topics: List[str] = []
        conferences: List[Dict[str, Any]] = []
        error: Optional[str] = None
        try:
            topics = fetch_conference_topics(self._hub_endpoint)
            conferences = fetch_conferences(self._hub_endpoint)
        except Exception as exc:
            LOGGER.warning("Conference fetch failed: %s", exc)
            error = str(exc)
        self.finished_with_data.emit(topics, conferences, error)


class ConferenceDialog(qt.QDialog):
    """Create or manage a Hub conference (VolView/OHIF parity)."""

    def __init__(
        self,
        parent: Optional[qt.QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(_("Conferencing"))
        self.setModal(False)
        self.setMinimumWidth(336)
        self.setMaximumWidth(400)

        self._hub_endpoint = ""
        self._session_topic = ""
        self._subscriber_name = ""
        self._connected = False
        self._available_topics: List[str] = []
        self._conferences: List[Dict[str, Any]] = []
        self._fetch_worker: Optional[_ConferenceFetchWorker] = None
        self._busy = False

        root = qt.QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(8)

        title_label = qt.QLabel(_("Conferencing"))
        font = title_label.font
        font.setBold(True)
        font.setPointSize(font.pointSize() + 2)
        title_label.setFont(font)
        root.addWidget(title_label)

        self._status_label = qt.QLabel("")
        self._status_label.setWordWrap(True)
        self._status_label.hide()
        root.addWidget(self._status_label)

        self._create_section = qt.QWidget()
        create_layout = qt.QVBoxLayout(self._create_section)
        create_layout.setContentsMargins(0, 0, 0, 0)
        create_layout.setSpacing(8)

        preset_label = qt.QLabel(_("Conference title"))
        preset_label.setStyleSheet("font-weight: 600; font-size: 12px;")
        create_layout.addWidget(preset_label)

        self._title_combo = qt.QComboBox()
        self._title_combo.addItem(_("Select a conference title"), "")
        for preset in HUB_CONFERENCE_TITLE_PRESETS:
            self._title_combo.addItem(preset, preset)
        self._title_combo.addItem(_("Other…"), "other")
        default_index = self._title_combo.findData("US annotations")
        if default_index >= 0:
            self._title_combo.setCurrentIndex(default_index)
        self._title_combo.currentIndexChanged.connect(self._on_title_preset_changed)
        create_layout.addWidget(self._title_combo)

        self._custom_title_edit = qt.QLineEdit()
        self._custom_title_edit.setPlaceholderText(_("Enter conference title"))
        self._custom_title_edit.hide()
        create_layout.addWidget(self._custom_title_edit)

        users_label = qt.QLabel(_("Users"))
        users_label.setStyleSheet("font-weight: 600; font-size: 12px;")
        create_layout.addWidget(users_label)

        self._topics_list = qt.QListWidget()
        self._topics_list.setMinimumHeight(80)
        self._topics_list.setMaximumHeight(132)
        self._topics_list.setSpacing(2)
        create_layout.addWidget(self._topics_list)

        self._create_button = qt.QPushButton(_("Create conference"))
        self._create_button.clicked.connect(self._on_create)
        create_layout.addWidget(self._create_button)

        root.addWidget(self._create_section)

        self._manage_section = qt.QWidget()
        manage_layout = qt.QVBoxLayout(self._manage_section)
        manage_layout.setContentsMargins(0, 0, 0, 0)
        manage_layout.setSpacing(8)

        manage_heading = qt.QLabel(_("Manage conference"))
        manage_heading.setStyleSheet("font-weight: 600; font-size: 12px;")
        manage_layout.addWidget(manage_heading)

        self._manage_info = qt.QLabel("")
        self._manage_info.setWordWrap(True)
        self._manage_info.setTextFormat(qt.Qt.RichText)
        self._manage_info.setStyleSheet("font-size: 12px;")
        manage_layout.addWidget(self._manage_info)

        self._exit_button = qt.QPushButton(_("Leave conference"))
        self._exit_button.clicked.connect(self._on_exit)
        manage_layout.addWidget(self._exit_button)

        self._manage_section.hide()
        root.addWidget(self._manage_section)

        close_button = qt.QPushButton(_("Close"))
        close_button.clicked.connect(self.close)
        root.addWidget(close_button)

    def configure(
        self,
        *,
        hub_endpoint: str,
        session_topic: str,
        subscriber_name: str,
        connected: bool,
    ) -> None:
        self._hub_endpoint = str(hub_endpoint or "").strip()
        self._session_topic = str(session_topic or "").strip()
        self._subscriber_name = str(subscriber_name or "").strip()
        self._connected = bool(connected)
        self._clear_status()

    def showEvent(self, event: qt.QShowEvent) -> None:
        super().showEvent(event)
        self._refresh_data()

    def _on_title_preset_changed(self) -> None:
        is_other = self._title_combo.currentData() == "other"
        self._custom_title_edit.setVisible(is_other)

    def _resolved_title(self) -> str:
        preset = str(self._title_combo.currentData() or "").strip()
        if preset == "other":
            return self._custom_title_edit.text.strip()
        return preset

    def _current_conference(self) -> Optional[Dict[str, Any]]:
        return find_active_cast_conference(
            self._session_topic,
            self._subscriber_name,
            self._conferences,
        )

    def _set_status(self, kind: str, message: str) -> None:
        if not message:
            self._status_label.hide()
            self._status_label.text = ""
            return
        self._status_label.show()
        self._status_label.text = message
        if kind == "success":
            self._status_label.setStyleSheet(_STATUS_STYLE_SUCCESS)
        elif kind == "error":
            self._status_label.setStyleSheet(_STATUS_STYLE_ERROR)
        else:
            self._status_label.setStyleSheet("")

    def _clear_status(self) -> None:
        self._set_status("", "")

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self._create_button.enabled = not busy and self._connected
        self._exit_button.enabled = not busy and self._connected

    def _set_topics_placeholder(self, message: str) -> None:
        self._topics_list.clear()
        item = qt.QListWidgetItem(message)
        item.setFlags(qt.Qt.NoItemFlags)
        self._topics_list.addItem(item)

    @staticmethod
    def _conference_users_text(conference: Dict[str, Any]) -> str:
        host = conference_host_topic(conference)
        raw_topics = conference.get("topics")
        attendees = (
            [str(entry).strip() for entry in raw_topics if str(entry).strip()]
            if isinstance(raw_topics, list)
            else []
        )
        users: List[str] = []
        if host:
            users.append(host)
        for entry in attendees:
            if entry not in users:
                users.append(entry)
        return ", ".join(users) if users else str(_("None"))

    def _refresh_data(self) -> None:
        if not self._hub_endpoint:
            self._set_status("error", _("No hub endpoint configured."))
            return
        if self._fetch_worker and self._fetch_worker.isRunning():
            return

        self._set_busy(True)
        self._set_topics_placeholder(_("Loading users…"))

        worker = _ConferenceFetchWorker(self._hub_endpoint, self)
        self._fetch_worker = worker
        worker.finished_with_data.connect(self._on_fetch_finished)
        worker.start()

    def _on_fetch_finished(
        self,
        topics: List[str],
        conferences: List[Dict[str, Any]],
        error: Optional[str],
    ) -> None:
        self._fetch_worker = None
        self._set_busy(False)
        self._available_topics = list(topics or [])
        self._conferences = list(conferences or [])

        if error:
            self._set_status("error", error)

        current = self._current_conference()
        if current:
            self._show_manage_mode(current)
            return

        self._show_create_mode()

    def _show_manage_mode(self, conference: Dict[str, Any]) -> None:
        self._create_section.hide()
        self._manage_section.show()

        host = conference_host_topic(conference) or "N/A"
        title = str(conference.get("title") or "N/A")
        users = self._conference_users_text(conference)

        self._manage_info.text = (
            f"<b>{_('Title:')}</b> {title}<br>"
            f"<b>{_('Host topic:')}</b> {host}<br>"
            f"<b>{_('Users:')}</b> {users}"
        )

        is_host = is_cast_conference_host(self._session_topic, conference)
        self._exit_button.text = (
            _("End conference") if is_host else _("Leave conference")
        )

    def _show_create_mode(self) -> None:
        self._manage_section.hide()
        self._create_section.show()

        host_topic = self._session_topic
        self._topics_list.clear()
        if not self._available_topics:
            self._set_topics_placeholder(_("No users available"))
        else:
            for entry in self._available_topics:
                item = qt.QListWidgetItem(entry)
                item.setFlags(qt.Qt.ItemIsEnabled | qt.Qt.ItemIsUserCheckable)
                checked = bool(
                    host_topic
                    and (
                        entry == host_topic
                        or entry.lower() == host_topic.lower()
                    )
                )
                item.setCheckState(
                    qt.Qt.Checked if checked else qt.Qt.Unchecked
                )
                self._topics_list.addItem(item)

        self._create_button.enabled = self._connected and not self._busy

    def _selected_topics(self) -> List[str]:
        selected: List[str] = []
        for index in range(self._topics_list.count):
            item = self._topics_list.item(index)
            if item is None:
                continue
            if item.flags() == qt.Qt.NoItemFlags:
                continue
            if item.checkState() == qt.Qt.Checked:
                selected.append(item.text())
        return selected

    def _on_create(self) -> None:
        if not self._connected:
            self._set_status("error", _("Connect to the Slicer hub first."))
            return
        host_topic = self._session_topic
        if not host_topic:
            self._set_status("error", _("Hub topic is required to host a conference."))
            return
        title = self._resolved_title()
        if not title:
            self._set_status("error", _("Conference title is required."))
            return
        selected = self._selected_topics()
        if not selected:
            self._set_status("error", _("Select at least one attendee topic."))
            return

        self._set_busy(True)
        self._clear_status()
        try:
            create_cast_conference(
                self._hub_endpoint,
                host_topic,
                title,
                selected,
            )
            self._set_status("success", _("Conference created."))
            self._refresh_data()
        except Exception as exc:
            LOGGER.warning("Create conference failed: %s", exc)
            self._set_status("error", str(exc) or _("Failed to create conference."))
        finally:
            self._set_busy(False)

    def _on_exit(self) -> None:
        conference = self._current_conference()
        if not conference:
            return
        host_topic = conference_host_topic(conference)
        if not host_topic:
            return

        is_host = is_cast_conference_host(self._session_topic, conference)
        leave_topic = None if is_host else self._session_topic

        self._set_busy(True)
        self._clear_status()
        try:
            delete_cast_conference(
                self._hub_endpoint,
                host_topic,
                leave_topic,
            )
            self._set_status(
                "success",
                _("Conference ended.") if is_host else _("Left conference."),
            )
            qt.QTimer.singleShot(HUB_CONFERENCE_EXIT_ACK_MS, self.close)
            self._refresh_data()
        except Exception as exc:
            LOGGER.warning("Exit conference failed: %s", exc)
            self._set_status("error", str(exc) or _("Failed to update conference."))
        finally:
            self._set_busy(False)

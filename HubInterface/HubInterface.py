"""3D Slicer scripted module: Slicer Hub."""

from __future__ import annotations

from Lib.repo_paths import ensure_monorepo_import_paths

ensure_monorepo_import_paths()

import ctk

from slicer.i18n import tr as _
from slicer.ScriptedLoadableModule import (
    ScriptedLoadableModule,
    ScriptedLoadableModuleWidget,
)

from Lib.HubServer import HubServerWidget
from Lib.ImageDisplayClient import ImageDisplayClientWidget
from Lib.ServiceProviders import ServiceProvidersWidget


class HubInterface(ScriptedLoadableModule):
    def __init__(self, parent):
        ScriptedLoadableModule.__init__(self, parent)
        self.parent.title = _("Slicer Hub")
        self.parent.categories = [_("Informatics")]
        self.parent.dependencies = []
        self.parent.contributors = ["ProjectWeek45"]
        self.parent.helpText = _(
            """
            Slicer Hub for 3D Slicer: Hub, Service Providers and  Image Display client .<br><br>
            Service Providers:
            Service providers subscribe to all user topics for dicom events and send back results to the user.
            Each service provider connects with its own product name and onMessage script.
            The script handles producing the results from the DICOM files received.
            <br><br>
            Image Display Client:
            The image display client provide a PACS client type interface to the 3D slicer viewer.  Supported events are ImagingStudy-open, ImagingStudy-close, and status-request.
            <br><br>
            Hub:
            The hub is the server that distributes the messages and handles the data transfer requests over the websocket connection to each client.
            """
        )
        self.parent.acknowledgementText = _(
            """
            Hub client protocol aligned with vtk-js HubClient. <br>
            The Slicer hub server lives in <code>HubInterface/hub/</code> (port 2018).<br>
            Standalone run: <code>python HubInterface/hub/hub.py --port 2018</code>.<br><br>
            Distributed under the MIT License.<br><br>
            <b>3D Slicer</b> &mdash; open-source platform for medical image computing from the
            <a href="https://www.slicer.org/">3D Slicer community</a>.
            """
        )


class HubInterfaceWidget(ScriptedLoadableModuleWidget):
    def __init__(self, parent=None) -> None:
        ScriptedLoadableModuleWidget.__init__(self, parent)
        self.serviceProvidersWidget = ServiceProvidersWidget()
        self.imageDisplayClientWidget = ImageDisplayClientWidget()
        self.hubWidget = HubServerWidget()

    @staticmethod
    def _ctk_collapsible_section(title: str, *, expanded: bool = True):
        section = ctk.ctkCollapsibleButton()
        section.text = title
        section.collapsed = not expanded
        return section

    def setup(self) -> None:
        ScriptedLoadableModuleWidget.setup(self)
        self.layout.setSpacing(10)

        clientSection = self._ctk_collapsible_section(
            _("Image Display Client"), expanded=True
        )
        self.layout.addWidget(clientSection)
        self.imageDisplayClientWidget.setup(clientSection)

        hubSection = self._ctk_collapsible_section(_("Hub"), expanded=False)
        self.layout.addWidget(hubSection)
        self.hubWidget.setup(hubSection)

        serviceProvidersSection = self._ctk_collapsible_section(
            _("Service Providers"), expanded=False
        )
        self.layout.addWidget(serviceProvidersSection)
        self.serviceProvidersWidget.setup(serviceProvidersSection)

        self.layout.addStretch(1)

    def cleanup(self) -> None:
        self.serviceProvidersWidget.cleanup()
        self.imageDisplayClientWidget.cleanup()
        self.hubWidget.cleanup()

    def enter(self) -> None:
        self.serviceProvidersWidget.enter()
        self.imageDisplayClientWidget.enter()
        self.hubWidget.enter()

    def exit(self) -> None:
        self.serviceProvidersWidget.exit()
        self.imageDisplayClientWidget.exit()
        self.hubWidget.exit()

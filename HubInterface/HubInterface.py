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
        self.parent.title = _("Hub server")
        self.parent.categories = [_("Informatics")]
        self.parent.dependencies = []
        self.parent.contributors = ["Martin Bellehumeur"]
        self.parent.helpText = _(
            """
            This extension is a 
            <a href="https://en.wikipedia.org/wiki/Publish%E2%80%93subscribe_pattern">WebSub-based</a>
            desktop integration messaging infrastructure that connects users of image displays,
            worklists, reporting tools, conferencing tools, remote inference models and AI Orchestrators.<br><br>
            Try the
            <a href="https://slicerhub-azejffgnb7dve8es.canadaeast-01.azurewebsites.net/worklist-client/">online example worklist client</a>
            or watch the
            <a href="https://youtu.be/xKQEOs1_9Bg">classroom vanishing brush tool</a>
            along with this documentation to find out more.<br><br>
            <b>Overview</b><br>
            The extension aims to promote, train, develop, and demonstrate interoperability in medical imaging applications.
            Its original intent was to support the
            <a href="https://profiles.ihe.net/RAD/IRA/">IHE Integrated Reporting Application</a>
            workflow. Over time, it has moved on to supporting the needs researchers and educators.
            Supporting <a href="https://wiki.ihe.net/index.php/AI_Results">IHE AI Results</a> and example reporting apps are WIP.<br><br>
            This python extension supplies the <b>HUB</b>, service provider example scripts, and the Slicer Qt
            <b>Image Display</b> client connection. Together with its sister repository of
            <a href="https://github.com/mbellehumeur/SlicerHub-js-clients">javascript clients</a>,
            the system includes:
            <ul>
            <li>a WebSub hub (<b>HUB</b> actor) for communication between applications and users</li>
            <li>a Worklist application (<b>WORKLIST_CLIENT</b> actor)</li>
            <li>Open-source medical imaging viewers (<b>IMAGE_DISPLAY</b> actors)</li>
            <li>Open-source medical imaging inference models (<b>EVIDENCE_CREATOR</b> actors)</li>
            <li>a DICOM SR reporting example (<b>REPORT_CREATOR</b> actor - WIP)</li>
            <li>the <a href="https://imaging.datacommons.cancer.gov/">Imaging Data Commons</a> and the
            <a href="https://www.slicer.org/">3D Slicer</a> DICOM DB as read-only image archives
            (<b>IMAGE_ARCHIVE</b> actor)</li>
            <li>an Authentication / identity provider with integration to the hub OIDC endpoints or built-in hub anonymous/mock authentication</li>
            </ul>
            The extension also includes features for education that are independent of the IHE Integrated Reporting workflow:
            <ul>
            <li>an example <a href="https://slicerhub-azejffgnb7dve8es.canadaeast-01.azurewebsites.net/classroom-client/">classroom application</a></li>
            <li>creating, saving, and uploading teaching files and cohorts for class preparation</li>
            <li>conferencing for collaborative viewing and teaching</li>
            <li>a <a href="https://youtu.be/xKQEOs1_9Bg?si=-gtvlB3aeq3KF-dX">vanishing brush tool</a> for temporary annotations during conferencing</li>
            <li>export to STL for 3D printing</li>
            </ul>
            Research and 3D Slicer functionalities, also independent of IHE, are included:
            <ul>
            <li>File transfer between applications</li>
            <li>Medical Reality scenes communication for conferencing</li>
            <li>SceneViews status on request with the long term goal of inference for multi-application hanging protocols.</li>
            <li>Status-request messaging for using current information from other applications in the workflow</li>
            <li>Slicer Qt Image Display client and DICOM DB study list (same python/websocket client)</li>
            </ul>
            File transfer allows inference servers to send their results directly back to the viewers without having to archived them to PACS first.
            Research file formats like NIfTI, NRRD, and Zarr can be used.
            Transfer is realized with multi-part POST to the hub and then GET retrieves from the receiving applications and service providers.
            The file naming policy is described
            <a href="https://github.com/mbellehumeur/SlicerHub/blob/main/docs/module/filename-policy.md">here</a>.<br><br>
            The extension javascript client libraries can be used to participate in IHE reporting workflows but it does not include a FHIR server or a FHIR Diagnostic Report Creator.<br><br>
            For open-source FHIRcast with a FHIR viewer and SMART authorization server, see the
            <a href="https://ohif.org/modes/fhir-viewer/">OHIF FHIR Viewer</a> mode or
            <a href="https://www.medplum.com/docs/fhircast">Medplum</a>.<br><br>
            For an overview of common features and differences of the Slicer Hub vs FHIRcast see
            <a href="https://github.com/mbellehumeur/SlicerHub/blob/main/docs/module/hub-description.md">docs/module/hub-description.md</a>.
            """
        )
        self.parent.acknowledgementText = _(
            """
            <b>License</b><br>
            Slicer Hub is distributed under the
            <a href="https://github.com/mbellehumeur/SlicerHub/blob/main/LICENSE.txt">MIT License</a>.<br><br>
            <b>Acknowledgements</b><br>
            <b>3D Slicer</b> &mdash; open-source platform for medical image computing from the
            <a href="https://www.slicer.org/">3D Slicer community</a>.
            This extension is built for and distributed through the 3D Slicer extension ecosystem.<br><br>
            <b>TotalSegmentator</b> was created by the Department of Research and Analysis at University Hospital Basel.
            If you use it, please cite our Radiology: Artificial Intelligence paper
            (<a href="https://arxiv.org/abs/2208.05868">free preprint</a>).
            If you use it for MR images, please cite the TotalSegmentator MRI <i>Radiology</i> paper
            (<a href="https://arxiv.org/abs/2405.19492">free preprint</a>).<br><br>
            <b>nnU-Net</b> &mdash; TotalSegmentator is heavily based on nnU-Net
            (<a href="https://arxiv.org/abs/1809.10486">preprint</a>).<br><br>
            <b>IDC Claude</b> &mdash; builds custom worklists from natural-language queries against the
            <a href="https://portal.imaging.datacommons.cancer.gov/">Imaging Data Commons</a>
            (National Cancer Institute) using Anthropic Claude. Query guidance follows the
            <a href="https://github.com/ImagingDataCommons/imaging-data-commons-skill">IDC skill</a>.<br><br>
            <b>idc-index</b> &mdash; official
            <a href="https://github.com/ImagingDataCommons/idc-index">Imaging Data Commons</a>
            Python package for local DuckDB SQL against IDC metadata and DICOM series download URLs;
            used by the IDC Claude service provider. If you use it in research, cite Fedorov A, et al.,
            <i>Radiographics</i> (<a href="https://doi.org/10.1148/rg.230180">2023</a>).<br><br>
            <b>OsiriX</b> &mdash; DICOM image processing software from
            <a href="https://www.osirix-viewer.com/">Pixmeo SARL</a>.<br><br>
            <b>OHIF</b> &mdash; open-source zero-footprint viewer from the
            <a href="https://ohif.org/">Open Health Imaging Foundation</a>.<br><br>
            <b>Slim</b> &mdash; interoperable slide microscopy viewer from the
            <a href="https://github.com/ImagingDataCommons/slim">Imaging Data Commons</a>
            (National Cancer Institute).<br><br>
            <b>Standards and trademarks</b><br>
            DICOM&reg; is the registered trademark of the National Electrical Manufacturers Association (NEMA)
            for its standards publications relating to digital imaging and communications in medicine.
            FHIR&reg; and related HL7 marks are registered trademarks of Health Level Seven International (HL7).
            IHE&reg; is a registered trademark of HIMSS. OsiriX&reg; is a trademark of Pixmeo SARL.
            OHIF&reg; is a trademark of the Open Health Imaging Foundation.
            Imaging Data Commons&reg; is a trademark of the National Cancer Institute.<br><br>
            The Slicer Hub (including its hub, clients, and documentation) references ideas, workflows, and vocabulary
            drawn from these standards&mdash;such as DICOM objects and metadata, FHIR and FHIRcast-style context and events,
            and IHE actor roles (for example, Image Display and Evidence Creator)&mdash;and product names such as OsiriX,
            OHIF, and Imaging Data Commons solely to describe interoperability behavior.<br><br>
            <b>Slicer Hub is not part of these standards.</b>
            It is not published by NEMA, HL7, or HIMSS, and is not an IHE Integration Profile, a FHIR implementation guide,
            or a DICOM conformance statement. Use of standard names and terms does not imply endorsement, certification,
            or official status. All other product and company names are trademarks of their respective owners.
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

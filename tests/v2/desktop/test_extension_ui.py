from __future__ import annotations

import os
import time
from pathlib import Path
from xml.etree import ElementTree as ET

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, QMetaObject, QObject, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQuick import QQuickItem

from mediaflow.desktop.app import configure_application_font, create_engine
from mediaflow.infrastructure.runtime_context import RuntimeContext
from tests.v2.infrastructure.test_media_pipeline import generate_black_intro_video


def _process_until(predicate, *, timeout: float = 20.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        QCoreApplication.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    return bool(predicate())


def _visual_item(root: QQuickItem, name: str) -> QQuickItem | None:
    for child in root.childItems():
        if child.objectName() == name:
            return child
        nested = _visual_item(child, name)
        if nested is not None:
            return nested
    return None


def test_extension_entrypoints_drive_real_project_results(tmp_path: Path) -> None:
    app = QGuiApplication.instance() or QGuiApplication([])
    configure_application_font(app)
    engine, controllers = create_engine(app)
    try:
        controllers.workspace.createProject(
            QUrl.fromLocalFile(str(tmp_path)).toString(),
            "Extension UI",
        )
        window = engine.rootObjects()[0]
        window.setWidth(1600)
        window.setHeight(980)
        assert _process_until(lambda: window.findChild(QQuickItem, "openMediaImportButton"))
        import_button = window.findChild(QQuickItem, "openMediaImportButton")
        assert import_button is not None and import_button.isVisible()
        assert window.findChild(QQuickItem, "openMobileImportButton") is None
        assert window.findChild(QQuickItem, "openStockMediaButton") is None
        assert window.findChild(QQuickItem, "openWebTemplateCenterButton") is None

        versions_button = window.findChild(QQuickItem, "openProjectVersionsButton")
        versions_dialog = window.findChild(QObject, "projectVersionsDialog")
        assert versions_button is not None and versions_button.isVisible()
        assert versions_dialog is not None and QMetaObject.invokeMethod(versions_button, "click")
        assert _process_until(lambda: versions_dialog.property("visible"))
        version_name = window.findChild(QQuickItem, "projectVersionNameInput")
        create_version = window.findChild(QQuickItem, "createProjectVersionButton")
        assert version_name is not None and create_version is not None
        version_name.setProperty("text", "导入前")
        assert QMetaObject.invokeMethod(create_version, "click")
        assert _process_until(lambda: len(controllers.workspace.projectVersions) == 1)
        snapshot = Path(controllers.workspace.projectVersions[0]["snapshotPath"])
        if not snapshot.is_absolute():
            snapshot = controllers.session.binding.current.project_dir / snapshot
        assert snapshot.is_file()
        assert QMetaObject.invokeMethod(versions_dialog, "close")

        source = tmp_path / "scene.mp4"
        generate_black_intro_video(source, RuntimeContext.discover().paths)
        controllers.media.importFiles(
            [QUrl.fromLocalFile(str(source))]
        )
        assert _process_until(lambda: controllers.media.assetsModel.rowCount() == 1)
        asset_id = controllers.media.assetsModel.get(0)["assetId"]
        controllers.timeline.dropAssets([asset_id], "", -1, 0, 3.0, 0, True, False)
        assert _process_until(lambda: controllers.timeline.clipsModel.rowCount() == 1)
        controllers.timeline.selectClip(controllers.timeline.clipsModel.get(0)["clipId"])

        detect_button = window.findChild(QQuickItem, "detectScenesButton")
        reframe_button = window.findChild(QQuickItem, "autoReframeButton")
        tracking_button = window.findChild(QQuickItem, "trackSubjectButton")
        assert all(
            button is not None and button.isVisible()
            for button in (detect_button, reframe_button, tracking_button)
        )
        assert QMetaObject.invokeMethod(detect_button, "click")
        assert _process_until(
            lambda: controllers.timeline.timelineMarkersModel.rowCount() > 0,
            timeout=60,
        )
        assert QMetaObject.invokeMethod(reframe_button, "click")
        assert _process_until(
            lambda: controllers.timeline.selectedClipData.get("transformKeyframeCount", 0) > 1,
            timeout=60,
        )
        assert QMetaObject.invokeMethod(tracking_button, "click")
        assert _process_until(
            lambda: controllers.timeline.selectedClipData.get("transformKeyframeSource")
            == "subject_tracking",
            timeout=60,
        )

        export_button = _visual_item(window.contentItem(), "titleExportButton")
        assert export_button is not None and QMetaObject.invokeMethod(export_button, "click")
        fcpxml_button = window.findChild(QQuickItem, "exportFcpxmlButton")
        assert fcpxml_button is not None and fcpxml_button.isVisible()
        fcpxml = tmp_path / "extension-ui.fcpxml"
        controllers.export.exportFcpxml(QUrl.fromLocalFile(str(fcpxml)).toString())
        assert fcpxml.is_file()
        root = ET.parse(fcpxml).getroot()
        resource = root.find("./resources/asset")
        assert resource is not None
        assert resource.find("media-rep") is not None
        if resource.attrib.get("hasAudio") == "1":
            linked_clip = root.find(".//asset-clip")
            assert linked_clip is not None
            assert linked_clip.attrib["ref"] == resource.attrib["id"]
        else:
            video_component = root.find(".//clip/video")
            assert video_component is not None
            assert video_component.attrib["ref"] == resource.attrib["id"]
            assert root.find(".//asset-clip") is None

        screenshot = tmp_path / "extension-entrypoints.png"
        rendered = window.grabWindow()
        assert not rendered.isNull() and rendered.save(str(screenshot))
    finally:
        controllers.shutdown()
        engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        QCoreApplication.processEvents()

    project_database = tmp_path / "Extension UI" / "project.mfp"
    release_probe = project_database.with_suffix(
        ".mfp.release-check"
    )
    project_database.replace(release_probe)
    release_probe.replace(project_database)

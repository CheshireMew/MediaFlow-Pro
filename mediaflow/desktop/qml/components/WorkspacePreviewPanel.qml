import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import ".."

Rectangle {
    id: root
    objectName: "previewPanel"

    required property var timelineView
    required property string sequenceName
    required property string activeMode
    required property var exportPreviewOptions
    property string previewMode: "program"
    readonly property bool compactSourceActions: width < 620
    readonly property var sourceMonitor: mediaflow.mediaController.sourceMonitorData
    readonly property alias viewport: previewViewport
    readonly property bool webInputActive: Boolean(
        webEditorLoader.item && webEditorLoader.item.webInputActive)

    color: Theme.surface
    radius: Theme.radius
    border.width: 1
    border.color: Theme.borderSubtle
    clip: true

    Connections {
        target: mediaflow.mediaController
        function onSourceMonitorChanged() {
            if (!root.sourceMonitor.assetId && root.previewMode === "source")
                root.previewMode = "program";
        }
    }

    Connections {
        target: mediaflow.workspacePlaybackController
        function onRemoteSeekRequested(frame) {
            previewViewport.seek(frame);
        }
        function onRemotePlayRequested(frame) {
            previewViewport.playPreviewFrom(frame);
        }
        function onRemotePauseRequested() {
            previewViewport.pause();
        }
        function onRemoteStopRequested() {
            previewViewport.stopPreview();
        }
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: 0

        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: Theme.workspacePanelHeaderHeight
            color: Theme.surface

            RowLayout {
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.leftMargin: 10
                anchors.rightMargin: 10
                anchors.verticalCenter: parent.verticalCenter
                spacing: 5

                AppButton {
                    objectName: "programMonitorTab"
                    text: qsTr("节目")
                    quiet: root.previewMode !== "program"
                    primary: root.previewMode === "program"
                    compact: true
                    onClicked: root.previewMode = "program"
                }
                AppButton {
                    objectName: "sourceMonitorTab"
                    text: root.compactSourceActions ? qsTr("源") : root.sourceMonitor.assetId
                        ? qsTr("源 · %1").arg(root.sourceMonitor.name) : qsTr("源")
                    quiet: root.previewMode !== "source"
                    primary: root.previewMode === "source"
                    compact: true
                    Layout.preferredWidth: root.compactSourceActions ? 56
                        : Math.min(220, implicitWidth)
                    Layout.maximumWidth: root.compactSourceActions ? 56 : 220
                    enabled: Boolean(root.sourceMonitor.assetId)
                    ToolTip.visible: root.compactSourceActions && hovered
                        && Boolean(root.sourceMonitor.assetId)
                    ToolTip.text: qsTr("源 · %1").arg(root.sourceMonitor.name)
                    onClicked: root.previewMode = "source"
                }
                Item { Layout.fillWidth: true }
                Text {
                    visible: root.previewMode === "program"
                    text: root.sequenceName
                    color: Theme.textMuted
                    font.pixelSize: Theme.fontSizeCaption
                    elide: Text.ElideRight
                    Layout.maximumWidth: 180
                }
                AppButton {
                    objectName: "sourceMarkInButton"
                    visible: root.previewMode === "source" && !root.compactSourceActions
                    text: qsTr("入点 %1").arg(root.sourceMonitor.inFrame || 0)
                    compact: true
                    quiet: true
                    onClicked: mediaflow.mediaController.setSourceInFrame(previewViewport.position)
                }
                AppButton {
                    objectName: "sourceMarkOutButton"
                    visible: root.previewMode === "source" && !root.compactSourceActions
                    text: qsTr("出点 %1").arg(root.sourceMonitor.outFrame || 0)
                    compact: true
                    quiet: true
                    onClicked: mediaflow.mediaController.setSourceOutFrame(previewViewport.position)
                }
                AppButton {
                    objectName: "sourceCaptureFrameButton"
                    visible: root.previewMode === "source" && !root.compactSourceActions
                    text: qsTr("截帧")
                    compact: true
                    quiet: true
                    enabled: mediaflow.workspaceViewController.actionCapabilities.canEdit
                    onClicked: mediaflow.mediaController.captureSourceFrame(previewViewport.position)
                }
                AppButton {
                    objectName: "sourceInsertRangeButton"
                    visible: root.previewMode === "source"
                    text: qsTr("插入选段")
                    compact: true
                    primary: true
                    enabled: mediaflow.workspaceViewController.actionCapabilities.canEdit
                    onClicked: mediaflow.mediaController.addSourceRangeToTimeline(
                        root.timelineView.visiblePlayheadFrame,
                        root.timelineView.pixelsPerFrame,
                        root.timelineView.snapEnabled)
                }
                AppIconButton {
                    id: sourceActionsMenuButton
                    objectName: "sourceActionsMenuButton"
                    visible: root.previewMode === "source" && root.compactSourceActions
                    iconName: "more"
                    compact: true
                    flat: true
                    Accessible.name: qsTr("更多源监视器操作")
                    toolTipText: Accessible.name

                    AppMenu {
                        id: sourceActionsMenu
                        y: sourceActionsMenuButton.height + 4
                        AppMenuItem {
                            text: qsTr("设置入点：%1").arg(root.sourceMonitor.inFrame || 0)
                            onTriggered: mediaflow.mediaController.setSourceInFrame(
                                previewViewport.position)
                        }
                        AppMenuItem {
                            text: qsTr("设置出点：%1").arg(root.sourceMonitor.outFrame || 0)
                            onTriggered: mediaflow.mediaController.setSourceOutFrame(
                                previewViewport.position)
                        }
                        AppMenuItem {
                            text: qsTr("截取当前帧")
                            enabled: mediaflow.workspaceViewController.actionCapabilities.canEdit
                            onTriggered: mediaflow.mediaController.captureSourceFrame(
                                previewViewport.position)
                        }
                    }
                    onClicked: sourceActionsMenu.open()
                }
            }

            Rectangle {
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.bottom: parent.bottom
                height: 1
                color: Theme.divider
            }
        }

        Item {
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.minimumHeight: 260

            PreviewViewport {
                id: previewViewport
                anchors.fill: parent
                visible: !(mediaflow.webController.isWebClip && mediaflow.webController.editMode)
                source: root.previewMode === "source"
                    ? String(root.sourceMonitor.graphPath || "")
                    : mediaflow.workspaceViewController.previewGraphPath
                runtimeRoot: mediaflow.workspaceViewController.mltRuntimeRoot
                mltLibrary: mediaflow.workspaceViewController.mltLibraryPath
                mltRepository: mediaflow.workspaceViewController.mltRepositoryPath
                mltData: mediaflow.workspaceViewController.mltDataPath
                hdrEnabled: mediaflow.workspaceViewController.colorMode === "hdr10_bt2020_pq"
                profileWidth: mediaflow.workspaceViewController.profileWidth
                profileHeight: mediaflow.workspaceViewController.profileHeight
                exportPreviewActive: root.previewMode === "program"
                    && root.activeMode === "export"
                transformInteractionEnabled: root.previewMode === "program"
                exportPreviewOptions: root.exportPreviewOptions
                subtitleText: root.previewMode === "source" ? ""
                    : root.activeMode === "export"
                    ? mediaflow.subtitleViewController.subtitleTextForTrackAtFrame(
                        String(root.exportPreviewOptions.burnSubtitleTrackId || ""), position)
                    : mediaflow.subtitleViewController.subtitleTextAtFrame(position)
                watermarkSource: root.previewMode === "program"
                    && root.activeMode === "export"
                    && root.exportPreviewOptions.watermark
                    && root.exportPreviewOptions.watermark.enabled
                    ? mediaflow.mediaController.assetUrl(String(
                        root.exportPreviewOptions.watermark.asset_id || "")) : ""
                onDroppedFramesReported: function (count) {
                    mediaflow.workspaceSequenceController.reportPreviewDroppedFrames(count);
                }
                onHdrActiveReported: function (active) {
                    mediaflow.workspaceSequenceController.reportHdrPreviewActive(active);
                }
            }

            Loader {
                id: webEditorLoader
                objectName: "webEditorLoader"
                anchors.fill: parent
                visible: mediaflow.webController.isWebClip && mediaflow.webController.editMode
                active: visible || status === Loader.Ready
                sourceComponent: WebEditorCanvas {
                    playheadFrame: previewViewport.position
                }
            }
        }
    }
}

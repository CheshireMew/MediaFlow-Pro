import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "."
import "components"

AppDialog {
    id: root
    objectName: "projectVersionsDialog"
    property string pendingRestoreId: ""
    property string pendingRestoreName: ""
    parent: Overlay.overlay
    anchors.centerIn: parent
    width: Math.min(560, parent ? parent.width - 48 : 560)
    height: Math.min(620, parent ? parent.height - 48 : 620)
    modal: true
    title: qsTr("命名版本")
    standardButtons: Dialog.Close

    contentItem: ColumnLayout {
        spacing: 10
        Text {
            Layout.fillWidth: true
            text: qsTr("命名版本保存项目数据库的完整快照。恢复后，时间线、字幕、网页素材状态和项目设置会一起回到该版本。")
            color: Theme.textMuted
            font.pixelSize: Theme.fontSizeCaption
            wrapMode: Text.WordWrap
        }
        RowLayout {
            Layout.fillWidth: true
            AppTextField {
                id: versionName
                objectName: "projectVersionNameInput"
                Layout.fillWidth: true
                enabled: Boolean(mediaflow.workspaceViewController.actionCapabilities.canEdit)
                placeholderText: qsTr("例如：客户审阅版")
            }
            AppButton {
                objectName: "createProjectVersionButton"
                primary: true
                text: qsTr("保存当前版本")
                enabled: versionName.text.trim().length > 0
                    && Boolean(mediaflow.workspaceViewController.actionCapabilities.canEdit)
                onClicked: {
                    mediaflow.workspaceProjectController.createNamedVersion(versionName.text);
                    versionName.clear();
                }
            }
        }
        Panel {
            Layout.fillWidth: true
            implicitHeight: projectTransferActions.implicitHeight + 18
            RowLayout {
                id: projectTransferActions
                anchors.fill: parent
                anchors.margins: 9
                spacing: 7
                AppButton {
                    Layout.fillWidth: true
                    text: qsTr("复制项目交接 CLI")
                    onClicked: mediaflow.automationController.copyProjectHandoffRequest()
                }
                AppButton {
                    Layout.fillWidth: true
                    text: qsTr("复制诊断包 CLI")
                    enabled: mediaflow.automationController.diagnosticsDefaultPath.length > 0
                    onClicked: mediaflow.automationController.copyDiagnosticsBundleRequest(
                        mediaflow.automationController.diagnosticsDefaultPath, [], false)
                }
                AppButton {
                    Layout.fillWidth: true
                    primary: true
                    text: qsTr("生成诊断包")
                    enabled: Boolean(mediaflow.workspaceViewController.actionCapabilities.canStartTasks)
                    onClicked: mediaflow.automationController.createDiagnosticsBundle(
                        mediaflow.automationController.diagnosticsDefaultPath, false)
                }
            }
        }
        ListView {
            id: versionList
            objectName: "projectVersionList"
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            spacing: 6
            model: mediaflow.workspaceViewController.projectVersions
            ScrollBar.vertical: AppScrollBar {}
            delegate: Rectangle {
                required property string versionId
                required property string name
                required property int contentRevision
                required property double createdAt
                width: versionList.width
                height: 64
                radius: Theme.radiusSmall
                color: Theme.surfaceRaised
                border.color: Theme.border
                RowLayout {
                    anchors.fill: parent
                    anchors.margins: 9
                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 2
                        Text {
                            Layout.fillWidth: true
                            text: name
                            color: Theme.text
                            font.pixelSize: Theme.fontSizeBodySmall
                            font.weight: Font.DemiBold
                            elide: Text.ElideRight
                        }
                        Text {
                            text: qsTr("项目修订 %1 · %2")
                                .arg(contentRevision)
                                .arg(new Date(createdAt).toLocaleString(Qt.locale()))
                            color: Theme.textMuted
                            font.pixelSize: Theme.fontSizeCaption
                        }
                    }
                    AppButton {
                        objectName: "restoreProjectVersionButton"
                        text: qsTr("恢复")
                        enabled: Boolean(mediaflow.workspaceViewController.actionCapabilities.canEdit)
                        onClicked: {
                            root.pendingRestoreId = versionId;
                            root.pendingRestoreName = name;
                            restoreConfirmation.open();
                        }
                    }
                }
            }
            EmptyState {
                anchors.fill: parent
                visible: versionList.count === 0
                iconName: "versions"
                title: qsTr("还没有命名版本")
                description: qsTr("在重要调整前保存一个版本，之后可以完整恢复。")
            }
        }
    }

    AppDialog {
        id: restoreConfirmation
        parent: root.parent
        anchors.centerIn: parent
        width: 430
        modal: true
        title: qsTr("恢复“%1”？").arg(root.pendingRestoreName)
        standardButtons: Dialog.Yes | Dialog.Cancel
        onAccepted: {
            if (mediaflow.workspaceViewController.actionCapabilities.canEdit)
                mediaflow.workspaceProjectController.restoreNamedVersion(root.pendingRestoreId);
            root.pendingRestoreId = "";
            root.pendingRestoreName = "";
        }
        contentItem: Text {
            width: 390
            text: qsTr("当前未命名的编辑会被该版本替换。已有命名版本和快照文件会保留。")
            color: Theme.text
            wrapMode: Text.WordWrap
        }
    }
}

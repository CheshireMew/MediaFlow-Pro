import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Dialogs
import "."
import "components"

Rectangle {
    id: root
    color: Theme.window
    property string pendingAction: ""
    property string pendingValue: ""

    function runPendingAction() {
        if (!projectController.hasProject)
            return
        if (pendingAction === "download")
            projectController.analyzeDownloadUrl(pendingValue)
        else if (pendingAction === "import")
            projectController.importMedia(pendingValue)
        pendingAction = ""
        pendingValue = ""
    }

    FolderDialog {
        id: createFolderDialog
        title: qsTr("选择项目保存位置")
        onAccepted: {
            projectController.createProject(selectedFolder.toString(), projectNameField.text)
            root.runPendingAction()
        }
    }
    FolderDialog {
        id: openFolderDialog
        title: qsTr("选择包含 project.mfp 的项目目录")
        onAccepted: {
            projectController.openProject(selectedFolder.toString())
            root.runPendingAction()
        }
    }
    FileDialog {
        id: importFileDialog
        title: qsTr("选择要导入的媒体")
        fileMode: FileDialog.OpenFile
        currentFolder: projectController.defaultImportDirectoryUrl
        nameFilters: [
            qsTr("媒体文件 (*.mp4 *.mov *.mkv *.webm *.avi *.mp3 *.wav *.flac *.aac *.m4a *.png *.jpg *.jpeg *.webp *.srt)"),
            qsTr("所有文件 (*)")
        ]
        onAccepted: {
            root.pendingAction = "import"
            root.pendingValue = selectedFile.toString()
            projectChoiceDialog.open()
        }
    }
    Dialog {
        id: projectChoiceDialog
        anchors.centerIn: parent
        implicitWidth: 430
        width: 430
        modal: true
        title: root.pendingAction === "download" ? qsTr("选择下载所属项目") : qsTr("选择素材所属项目")
        contentItem: ColumnLayout {
            spacing: 10
            Text {
                Layout.preferredWidth: 390
                text: qsTr("所有素材和任务必须属于一个项目。可以使用左侧填写的名称新建项目，也可以打开已有项目。")
                color: Theme.textMuted
                wrapMode: Text.WordWrap
            }
            RowLayout {
                Layout.fillWidth: true
                AppButton {
                    Layout.fillWidth: true
                    primary: true
                    text: qsTr("新建项目")
                    enabled: projectNameField.text.trim().length > 0
                    onClicked: { projectChoiceDialog.close(); createFolderDialog.open() }
                }
                AppButton {
                    Layout.fillWidth: true
                    text: qsTr("打开项目")
                    onClicked: { projectChoiceDialog.close(); openFolderDialog.open() }
                }
            }
        }
    }

    RowLayout {
        anchors.fill: parent
        anchors.margins: 52
        spacing: 54

        ColumnLayout {
            Layout.preferredWidth: 470
            Layout.maximumWidth: 520
            spacing: 18

            Rectangle {
                width: 58
                height: 58
                radius: 18
                color: Theme.accent
                Text {
                    anchors.centerIn: parent
                    text: "M"
                    color: "white"
                    font.pixelSize: 28
                    font.weight: Font.Bold
                }
            }
            Text {
                text: qsTr("MediaFlow Pro")
                color: Theme.text
                font.pixelSize: 38
                font.weight: Font.Bold
            }
            Text {
                Layout.maximumWidth: 470
                text: qsTr("从下载、转录和翻译，到多轨编辑与专业导出。所有工作都保存在一个可移动的项目目录中。")
                color: Theme.textMuted
                font.pixelSize: 16
                lineHeight: 1.45
                wrapMode: Text.WordWrap
            }

            Item { height: 12 }

            Text {
                text: qsTr("新建项目")
                color: Theme.text
                font.pixelSize: 15
                font.weight: Font.DemiBold
            }
            TextField {
                id: projectNameField
                Layout.fillWidth: true
                implicitHeight: 42
                placeholderText: qsTr("例如：产品发布视频")
                color: Theme.text
                placeholderTextColor: Theme.textMuted
                leftPadding: 14
                background: Rectangle {
                    radius: Theme.radiusSmall
                    color: Theme.surfaceRaised
                    border.color: projectNameField.activeFocus ? Theme.accent : Theme.border
                }
                onAccepted: if (text.trim().length > 0) createFolderDialog.open()
            }
            RowLayout {
                Layout.fillWidth: true
                AppButton {
                    Layout.fillWidth: true
                    primary: true
                    text: qsTr("选择位置并创建")
                    enabled: projectNameField.text.trim().length > 0
                    onClicked: createFolderDialog.open()
                }
                AppButton {
                    text: qsTr("打开已有项目")
                    onClicked: openFolderDialog.open()
                }
            }

            Panel {
                Layout.fillWidth: true
                implicitHeight: 132
                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 12
                    spacing: 8
                    Text { text: qsTr("快速开始"); color: Theme.text; font.pixelSize: 13; font.weight: Font.DemiBold }
                    TextField {
                        id: downloadUrlField
                        Layout.fillWidth: true
                        implicitHeight: 38
                        placeholderText: qsTr("粘贴视频或播放列表链接")
                        color: Theme.text
                        placeholderTextColor: Theme.textMuted
                        leftPadding: 12
                        background: Rectangle {
                            radius: Theme.radiusSmall
                            color: Theme.window
                            border.color: downloadUrlField.activeFocus ? Theme.accent : Theme.border
                        }
                        onAccepted: if (text.trim().length > 0) {
                            root.pendingAction = "download"
                            root.pendingValue = text.trim()
                            projectChoiceDialog.open()
                        }
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        AppButton {
                            Layout.fillWidth: true
                            primary: true
                            text: qsTr("选择项目并下载")
                            enabled: downloadUrlField.text.trim().length > 0
                            onClicked: {
                                root.pendingAction = "download"
                                root.pendingValue = downloadUrlField.text.trim()
                                projectChoiceDialog.open()
                            }
                        }
                        AppButton {
                            Layout.fillWidth: true
                            text: qsTr("导入本地素材")
                            onClicked: importFileDialog.open()
                        }
                    }
                }
            }
        }

        Panel {
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.minimumWidth: 460

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 26
                spacing: 16

                RowLayout {
                    Layout.fillWidth: true
                    Text {
                        text: qsTr("最近项目")
                        color: Theme.text
                        font.pixelSize: 18
                        font.weight: Font.DemiBold
                    }
                    Item { Layout.fillWidth: true }
                    Text {
                        text: qsTr("项目目录可直接复制或移动")
                        color: Theme.textMuted
                        font.pixelSize: 12
                    }
                }

                Rectangle {
                    Layout.fillWidth: true
                    implicitHeight: 64
                    radius: Theme.radius
                    color: Theme.surfaceRaised
                    border.color: projectController.homeSummary.failedTaskCount > 0
                                  || projectController.homeSummary.offlineAssetCount > 0
                                  ? Theme.warning : Theme.border
                    RowLayout {
                        anchors.fill: parent
                        anchors.margins: 12
                        spacing: 14
                        Text { text: qsTr("任务与提醒"); color: Theme.text; font.weight: Font.DemiBold }
                        Text { text: qsTr("运行 %1").arg(projectController.homeSummary.runningTaskCount); color: Theme.accentHover }
                        Text { text: qsTr("失败 %1").arg(projectController.homeSummary.failedTaskCount); color: projectController.homeSummary.failedTaskCount > 0 ? Theme.danger : Theme.textMuted }
                        Text { text: qsTr("离线素材 %1").arg(projectController.homeSummary.offlineAssetCount); color: projectController.homeSummary.offlineAssetCount > 0 ? Theme.warning : Theme.textMuted }
                        Text { text: qsTr("待确认 %1").arg(projectController.homeSummary.pendingWorkflowCount); color: Theme.textMuted }
                        Item { Layout.fillWidth: true }
                        Text { text: qsTr("最近产物 %1").arg(projectController.homeSummary.recentArtifactCount); color: Theme.textMuted }
                    }
                }

                ListView {
                    id: recentList
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    spacing: 8
                    clip: true
                    model: projectController.recentProjectsModel
                    delegate: Rectangle {
                        required property string name
                        required property string path
                        required property bool available
                        required property int runningTaskCount
                        required property int failedTaskCount
                        required property int offlineAssetCount
                        required property int pendingWorkflowCount
                        required property string recentArtifact
                        width: recentList.width
                        height: 96
                        radius: Theme.radius
                        color: recentMouse.containsMouse && available ? Theme.surfaceHover : Theme.surfaceRaised
                        border.color: available ? Theme.border : Theme.danger
                        opacity: available ? 1.0 : 0.7
                        activeFocusOnTab: available
                        Accessible.name: qsTr("项目 %1").arg(name)
                        Accessible.role: Accessible.Button
                        Keys.onReturnPressed: if (available) projectController.openProject(path)
                        Keys.onSpacePressed: if (available) projectController.openProject(path)
                        RowLayout {
                            anchors.fill: parent
                            anchors.margins: 14
                            spacing: 12
                            Rectangle {
                                width: 44; height: 44; radius: 11
                                color: available ? Theme.accentSoft : "#402127"
                                Text { anchors.centerIn: parent; text: available ? "▶" : "!"; color: available ? Theme.accentHover : Theme.danger }
                            }
                            ColumnLayout {
                                Layout.fillWidth: true; spacing: 4
                                Text { text: name; color: Theme.text; font.pixelSize: 13; font.weight: Font.DemiBold }
                                Text { Layout.fillWidth: true; text: path; color: Theme.textMuted; font.pixelSize: 10; elide: Text.ElideMiddle }
                                Text { visible: !available; text: qsTr("项目已移动或不可用"); color: Theme.danger; font.pixelSize: 9 }
                                Text {
                                    visible: available
                                    text: qsTr("运行 %1 · 失败 %2 · 离线 %3 · 待确认 %4")
                                        .arg(runningTaskCount).arg(failedTaskCount)
                                        .arg(offlineAssetCount).arg(pendingWorkflowCount)
                                    color: failedTaskCount > 0 || offlineAssetCount > 0 ? Theme.warning : Theme.textMuted
                                    font.pixelSize: 9
                                }
                            }
                            ColumnLayout {
                                Text { text: available ? qsTr("打开 ›") : qsTr("离线"); color: available ? Theme.accentHover : Theme.danger; font.pixelSize: 11 }
                                AppButton {
                                    visible: recentArtifact.length > 0
                                    text: qsTr("最近产物")
                                    onClicked: projectController.openArtifact(recentArtifact)
                                }
                            }
                        }
                        MouseArea {
                            id: recentMouse
                            anchors.fill: parent
                            anchors.rightMargin: recentArtifact.length > 0 ? 104 : 0
                            enabled: available
                            hoverEnabled: true
                            cursorShape: enabled ? Qt.PointingHandCursor : Qt.ArrowCursor
                            onClicked: projectController.openProject(path)
                        }
                    }
                }

                EmptyState {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    visible: recentList.count === 0
                    iconText: "▣"
                    title: qsTr("还没有最近项目")
                    description: qsTr("创建第一个项目后，下载、字幕、短视频和导出结果都会集中保存在项目目录中。")
                }

                Rectangle {
                    Layout.fillWidth: true
                    implicitHeight: 86
                    radius: Theme.radius
                    color: "#101b27"
                    border.color: "#234568"
                    RowLayout {
                        anchors.fill: parent
                        anchors.margins: 16
                        spacing: 14
                        Text { text: "✓"; color: Theme.success; font.pixelSize: 22 }
                        ColumnLayout {
                            Layout.fillWidth: true
                            Text { text: qsTr("纯本地项目"); color: Theme.text; font.weight: Font.DemiBold }
                            Text {
                                Layout.fillWidth: true
                                text: qsTr("不启动本地 Web 服务，也不打开浏览器窗口。下载由 yt-dlp 直接完成。")
                                color: Theme.textMuted
                                font.pixelSize: 12
                                wrapMode: Text.WordWrap
                            }
                        }
                    }
                }
            }
        }
    }
}

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Dialogs
import "."
import "components"

Rectangle {
    id: root
    objectName: "homeView"
    color: Theme.window

    Timer {
        id: downloadUrlPersistenceTimer
        interval: 400
        repeat: false
        onTriggered: settingsController.setLastDownloadUrl(downloadUrlField.text)
    }

    function createProject() {
        workspaceController.createProjectInDefaultDirectory(createProjectNameField.text.trim())
        if (workspaceController.hasProject) {
            createProjectNameField.clear()
            createProjectDialog.close()
        }
    }

    Shortcut {
        sequence: StandardKey.New
        onActivated: createProjectDialog.open()
    }
    Shortcut {
        sequence: StandardKey.Open
        onActivated: openFolderDialog.open()
    }

    FolderDialog {
        id: openFolderDialog
        title: qsTr("选择包含 project.mfp 的项目目录")
        onAccepted: {
            workspaceController.openProject(selectedFolder.toString())
        }
    }
    Dialog {
        id: createProjectDialog
        objectName: "createProjectDialog"
        anchors.centerIn: parent
        implicitWidth: 430
        width: 430
        modal: true
        title: qsTr("新建项目")
        onOpened: createProjectNameField.forceActiveFocus()
        contentItem: ColumnLayout {
            spacing: 12
            Text {
                text: qsTr("项目名称（选填）")
                color: Theme.text
                font.pixelSize: Theme.fontSizeBody
                font.weight: Font.DemiBold
            }
            AppTextField {
                id: createProjectNameField
                objectName: "createProjectNameField"
                Layout.fillWidth: true
                implicitHeight: 42
                placeholderText: qsTr("留空将自动使用“未命名项目 1、2…”")
                onAccepted: root.createProject()
            }
            RowLayout {
                Layout.fillWidth: true
                Item { Layout.fillWidth: true }
                AppButton {
                    text: qsTr("取消")
                    onClicked: createProjectDialog.close()
                }
                AppButton {
                    objectName: "confirmCreateProjectButton"
                    primary: true
                    text: qsTr("创建项目")
                    onClicked: root.createProject()
                }
            }
        }
    }
    Flickable {
        id: homeScroll
        objectName: "homeScroll"
        anchors.fill: parent
        clip: true
        contentWidth: width
        contentHeight: Math.max(height, homeContent.y + homeContent.implicitHeight + 36)
        boundsBehavior: Flickable.StopAtBounds
        ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

        ColumnLayout {
            id: homeContent
            objectName: "homeContent"
            width: Math.max(0, Math.min(1920, root.width - 64))
            x: Math.round((root.width - width) / 2)
            y: 34
            spacing: 20

            ColumnLayout {
                Layout.fillWidth: true
                spacing: 18

                Button {
                    id: createHero
                    objectName: "homeCreateHero"
                    Layout.fillWidth: true
                    Layout.preferredHeight: 190
                    padding: 0
                    hoverEnabled: true
                    focusPolicy: Qt.StrongFocus
                    Accessible.name: qsTr("新建项目")
                    Accessible.description: qsTr("创建空白项目后，可以导入本地媒体，或把文件直接拖入时间线。")
                    scale: down ? 0.997 : 1.0
                    Behavior on scale { NumberAnimation { duration: 80 } }
                    onClicked: createProjectDialog.open()

                    HoverHandler {
                        cursorShape: Qt.PointingHandCursor
                    }

                    background: Rectangle {
                        radius: Theme.radiusLarge
                        clip: true
                        border.width: createHero.activeFocus ? 2 : 1
                        border.color: createHero.activeFocus || createHero.hovered ? Theme.accent : "#34536a"
                        gradient: Gradient {
                            orientation: Gradient.Horizontal
                            GradientStop { position: 0.0; color: "#26364e" }
                            GradientStop { position: 0.48; color: "#15515a" }
                            GradientStop { position: 1.0; color: "#171b24" }
                        }

                        Rectangle {
                            width: 360
                            height: 360
                            x: -110
                            y: -210
                            radius: 180
                            color: "#6c4a82"
                            opacity: 0.22
                        }
                        Rectangle {
                            width: 420
                            height: 420
                            anchors.right: parent.right
                            anchors.rightMargin: -140
                            anchors.verticalCenter: parent.verticalCenter
                            radius: 210
                            color: "#0b6972"
                            opacity: 0.18
                        }
                    }

                    contentItem: Item {
                        ColumnLayout {
                            anchors.centerIn: parent
                            width: Math.min(900, parent.width - 96)
                            spacing: 14
                            RowLayout {
                                Layout.alignment: Qt.AlignHCenter
                                spacing: 16
                                Rectangle {
                                    id: createHeroIcon
                                    objectName: "createProjectHeroIcon"
                                    implicitWidth: 58
                                    implicitHeight: 58
                                    radius: 16
                                    color: "#e8f5f5"
                                    Text {
                                        anchors.centerIn: parent
                                        text: "+"
                                        color: "#173441"
                                        font.pixelSize: 32
                                        font.weight: Font.Bold
                                    }
                                }
                                Text {
                                    id: createHeroTitle
                                    objectName: "createProjectHeroTitle"
                                    text: qsTr("新建项目")
                                    color: "white"
                                    font.pixelSize: Theme.fontSizeDisplay + 6
                                    font.weight: Font.Bold
                                }
                            }
                            Text {
                                Layout.fillWidth: true
                                text: qsTr("创建空白项目后，可以导入本地媒体，或把文件直接拖入时间线。")
                                color: "#c5d1dc"
                                font.pixelSize: Theme.fontSizeBodyLarge
                                horizontalAlignment: Text.AlignHCenter
                                wrapMode: Text.WordWrap
                            }
                        }
                    }
                }

                RowLayout {
                    Layout.fillWidth: true
                    Layout.preferredHeight: 126
                    spacing: 14

                    Panel {
                        Layout.fillWidth: true
                        Layout.preferredWidth: 700
                        Layout.fillHeight: true
                        level: 1
                        ColumnLayout {
                            anchors.fill: parent
                            anchors.margins: 16
                            spacing: 9
                            Text {
                                text: qsTr("快速开始")
                                color: Theme.text
                                font.pixelSize: Theme.fontSizeBodyLarge
                                font.weight: Font.DemiBold
                            }
                            RowLayout {
                                Layout.fillWidth: true
                                AppTextField {
                                    id: downloadUrlField
                                    objectName: "downloadUrlField"
                                    Layout.fillWidth: true
                                    placeholderText: qsTr("粘贴视频或播放列表链接")
                                    Component.onCompleted: text = String(
                                        settingsController.settingsData.lastDownloadUrl || "")
                                    onTextEdited: downloadUrlPersistenceTimer.restart()
                                    onAccepted: {
                                        downloadUrlPersistenceTimer.stop()
                                        settingsController.setLastDownloadUrl(text)
                                        if (text.trim().length > 0
                                                && !taskController.downloadAnalysisBusy)
                                            taskController.analyzeDownloadUrl(text.trim())
                                    }
                                }
                                AppButton {
                                    objectName: "pasteDownloadUrlButton"
                                    text: qsTr("粘贴")
                                    onClicked: {
                                        downloadUrlField.forceActiveFocus()
                                        downloadUrlField.selectAll()
                                        downloadUrlField.paste()
                                        downloadUrlPersistenceTimer.restart()
                                    }
                                }
                                AppButton {
                                    objectName: "quickStartDownloadButton"
                                    primary: true
                                    text: taskController.downloadAnalysisBusy
                                          ? qsTr("正在读取视频信息…")
                                          : qsTr("下载并新建项目")
                                    enabled: downloadUrlField.text.trim().length > 0
                                             && !taskController.downloadAnalysisBusy
                                    onClicked: {
                                        downloadUrlPersistenceTimer.stop()
                                        settingsController.setLastDownloadUrl(downloadUrlField.text)
                                        taskController.analyzeDownloadUrl(downloadUrlField.text.trim())
                                    }
                                }
                            }
                        }
                    }

                    Panel {
                        Layout.fillWidth: true
                        Layout.preferredWidth: 320
                        Layout.fillHeight: true
                        level: 1
                        ColumnLayout {
                            anchors.fill: parent
                            anchors.margins: 16
                            spacing: 8
                            Text {
                                text: qsTr("打开已有项目")
                                color: Theme.text
                                font.pixelSize: Theme.fontSizeBodyLarge
                                font.weight: Font.DemiBold
                            }
                            Text {
                                Layout.fillWidth: true
                                text: qsTr("打开包含 project.mfp 的项目目录，继续之前的工作。")
                                color: Theme.textMuted
                                font.pixelSize: Theme.fontSizeCaption
                                wrapMode: Text.WordWrap
                            }
                            Item { Layout.fillHeight: true }
                            AppButton {
                                objectName: "openExistingProjectButton"
                                Layout.fillWidth: true
                                text: qsTr("打开已有项目")
                                onClicked: openFolderDialog.open()
                            }
                        }
                    }
                }
        }

        Panel {
            id: recentSection
            objectName: "homeRecentSection"
            Layout.fillWidth: true
            Layout.preferredHeight: Math.max(380, root.height - 420)
            level: 1

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 16
                spacing: 14

                RowLayout {
                    Layout.fillWidth: true
                    Layout.preferredHeight: 34
                    spacing: 10
                    Text {
                        text: qsTr("最近项目")
                        color: Theme.text
                        font.pixelSize: Theme.fontSizeTitle
                        font.weight: Font.DemiBold
                    }
                    Rectangle {
                        visible: recentList.count > 0
                        implicitWidth: projectCountText.implicitWidth + 14
                        implicitHeight: 24
                        radius: 12
                        color: Theme.surfaceHover
                        border.color: Theme.border
                        Text {
                            id: projectCountText
                            anchors.centerIn: parent
                            text: recentList.count
                            color: Theme.textMuted
                            font.pixelSize: Theme.fontSizeCaption
                            font.weight: Font.DemiBold
                        }
                    }
                    Item { Layout.fillWidth: true }
                    Text {
                        text: qsTr("项目目录可直接复制或移动")
                        color: Theme.textMuted
                        font.pixelSize: Theme.fontSizeBodySmall
                    }
                }

                Rectangle {
                    id: projectHealth
                    Layout.fillWidth: true
                    Layout.preferredHeight: 36
                    visible: workspaceController.homeSummary.runningTaskCount > 0
                        || workspaceController.homeSummary.failedTaskCount > 0
                        || workspaceController.homeSummary.offlineAssetCount > 0
                        || workspaceController.homeSummary.pendingWorkflowCount > 0
                    radius: 18
                    color: "#191f26"
                    border.color: workspaceController.homeSummary.failedTaskCount > 0
                        || workspaceController.homeSummary.offlineAssetCount > 0
                        ? "#74542f" : Theme.border
                    RowLayout {
                        anchors.fill: parent
                        anchors.leftMargin: 14
                        anchors.rightMargin: 14
                        spacing: 12
                        Rectangle {
                            width: 6
                            height: 6
                            radius: 3
                            color: workspaceController.homeSummary.failedTaskCount > 0
                                || workspaceController.homeSummary.offlineAssetCount > 0
                                ? Theme.warning : Theme.accentHover
                        }
                        Text { text: qsTr("任务与提醒"); color: Theme.text; font.pixelSize: Theme.fontSizeBodySmall; font.weight: Font.DemiBold }
                        Text { text: qsTr("运行 %1").arg(workspaceController.homeSummary.runningTaskCount); color: Theme.accentHover }
                        Text { text: qsTr("失败 %1").arg(workspaceController.homeSummary.failedTaskCount); color: workspaceController.homeSummary.failedTaskCount > 0 ? Theme.danger : Theme.textMuted }
                        Text { text: qsTr("离线素材 %1").arg(workspaceController.homeSummary.offlineAssetCount); color: workspaceController.homeSummary.offlineAssetCount > 0 ? Theme.warning : Theme.textMuted }
                        Text { text: qsTr("待确认 %1").arg(workspaceController.homeSummary.pendingWorkflowCount); color: Theme.textMuted }
                        Item { Layout.fillWidth: true }
                    }
                }

                GridView {
                    id: recentList
                    objectName: "recentProjectGrid"
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    visible: count > 0
                    property int columnCount: Math.max(1, Math.floor(width / 250))
                    cellWidth: width / columnCount
                    cellHeight: Math.round(cellWidth * 1.12)
                    clip: true
                    interactive: contentHeight > height
                    boundsBehavior: Flickable.StopAtBounds
                    reuseItems: true
                    model: workspaceController.recentProjectsModel
                    ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }
                    delegate: Rectangle {
                        objectName: "recentProjectCard"
                        required property string name
                        required property string path
                        required property bool available
                        required property int runningTaskCount
                        required property int failedTaskCount
                        required property int offlineAssetCount
                        required property int pendingWorkflowCount
                        required property string recentArtifact
                        required property string coverUrl
                        property bool needsAttention: !available || failedTaskCount > 0
                            || offlineAssetCount > 0 || pendingWorkflowCount > 0
                        property string projectStateText: {
                            if (!available)
                                return qsTr("项目已移动或不可用")
                            if (failedTaskCount > 0)
                                return qsTr("失败 %1").arg(failedTaskCount)
                            if (offlineAssetCount > 0)
                                return qsTr("离线素材 %1").arg(offlineAssetCount)
                            if (pendingWorkflowCount > 0)
                                return qsTr("待确认 %1").arg(pendingWorkflowCount)
                            if (runningTaskCount > 0)
                                return qsTr("运行 %1").arg(runningTaskCount)
                            return "MEDIAFLOW PROJECT"
                        }
                        x: 6
                        width: recentList.cellWidth - 12
                        height: recentList.cellHeight - 12
                        radius: 11
                        color: Theme.surface
                        border.color: !available ? Theme.danger
                            : activeFocus || recentMouse.containsMouse ? Theme.accent : Theme.border
                        border.width: activeFocus ? 2 : 1
                        opacity: available ? 1.0 : 0.7
                        clip: true
                        scale: recentMouse.containsMouse && available ? 1.012 : 1.0
                        z: recentMouse.containsMouse ? 2 : 0
                        activeFocusOnTab: available
                        Accessible.name: qsTr("项目 %1").arg(name)
                        Accessible.role: Accessible.Button
                        Keys.onReturnPressed: if (available) workspaceController.openProject(path)
                        Keys.onSpacePressed: if (available) workspaceController.openProject(path)
                        MouseArea {
                            id: recentMouse
                            anchors.fill: parent
                            enabled: available
                            hoverEnabled: true
                            cursorShape: enabled ? Qt.PointingHandCursor : Qt.ArrowCursor
                            onClicked: workspaceController.openProject(path)
                        }

                        Behavior on scale {
                            NumberAnimation { duration: 120; easing.type: Easing.OutCubic }
                        }

                        Rectangle {
                            id: projectPreview
                            objectName: "recentProjectPreview"
                            anchors.left: parent.left
                            anchors.right: parent.right
                            anchors.top: parent.top
                            anchors.margins: 6
                            height: Math.round(width * 3 / 4)
                            radius: 9
                            color: Theme.surfaceSunken
                            clip: true

                            Rectangle {
                                anchors.fill: parent
                                gradient: Gradient {
                                    orientation: Gradient.Horizontal
                                    GradientStop { position: 0.0; color: "#233650" }
                                    GradientStop { position: 0.55; color: "#16454e" }
                                    GradientStop { position: 1.0; color: "#20202d" }
                                }
                                Rectangle {
                                    width: parent.width * 0.58
                                    height: width
                                    x: -width * 0.32
                                    y: -height * 0.58
                                    radius: width / 2
                                    color: "#815b91"
                                    opacity: 0.20
                                }
                                Rectangle {
                                    width: parent.width * 0.66
                                    height: width
                                    anchors.right: parent.right
                                    anchors.rightMargin: -width * 0.35
                                    anchors.bottom: parent.bottom
                                    anchors.bottomMargin: -height * 0.55
                                    radius: width / 2
                                    color: "#13828a"
                                    opacity: 0.18
                                }
                                Column {
                                    anchors.centerIn: parent
                                    spacing: 8
                                    Rectangle {
                                        anchors.horizontalCenter: parent.horizontalCenter
                                        width: 46
                                        height: 46
                                        radius: 14
                                        color: "#e8f5f5"
                                        Text {
                                            anchors.centerIn: parent
                                            text: "M"
                                            color: "#173441"
                                            font.pixelSize: 22
                                            font.weight: Font.Bold
                                        }
                                    }
                                    Text {
                                        anchors.horizontalCenter: parent.horizontalCenter
                                        text: "MEDIAFLOW"
                                        color: "#d9e6ea"
                                        opacity: 0.72
                                        font.pixelSize: Theme.fontSizeCaption
                                        font.letterSpacing: 1.5
                                    }
                                }
                            }

                            Image {
                                id: projectCover
                                objectName: "recentProjectCover"
                                anchors.fill: parent
                                source: coverUrl
                                sourceSize.width: 640
                                sourceSize.height: 360
                                fillMode: Image.PreserveAspectCrop
                                asynchronous: true
                                cache: true
                                visible: status === Image.Ready
                            }

                            Rectangle {
                                anchors.fill: parent
                                visible: projectCover.status === Image.Ready
                                gradient: Gradient {
                                    orientation: Gradient.Vertical
                                    GradientStop { position: 0.55; color: "#00000000" }
                                    GradientStop { position: 1.0; color: "#72000000" }
                                }
                            }

                            Rectangle {
                                anchors.centerIn: parent
                                width: 48
                                height: 48
                                radius: 24
                                visible: recentMouse.containsMouse && available
                                color: "#d9eaf2f7"
                                border.color: "#5fffffff"
                                Text {
                                    anchors.centerIn: parent
                                    anchors.horizontalCenterOffset: 1
                                    text: "▶"
                                    color: "#14202a"
                                    font.pixelSize: 18
                                }
                            }

                            Rectangle {
                                anchors.top: parent.top
                                anchors.right: parent.right
                                anchors.margins: 10
                                implicitWidth: openLabel.implicitWidth + 16
                                implicitHeight: 26
                                radius: 13
                                color: "#b8181d23"
                                border.color: "#34ffffff"
                                visible: recentMouse.containsMouse || !available
                                Text {
                                    id: openLabel
                                    anchors.centerIn: parent
                                    text: available ? qsTr("打开 ›") : qsTr("离线")
                                    color: available ? Theme.text : Theme.danger
                                    font.pixelSize: Theme.fontSizeCaption
                                    font.weight: Font.DemiBold
                                }
                            }
                        }

                        ColumnLayout {
                            anchors.left: parent.left
                            anchors.right: parent.right
                            anchors.top: projectPreview.bottom
                            anchors.bottom: parent.bottom
                            anchors.leftMargin: 12
                            anchors.rightMargin: 12
                            anchors.topMargin: 10
                            anchors.bottomMargin: 10
                            spacing: 5
                            RowLayout {
                                Layout.fillWidth: true
                                Text {
                                    Layout.fillWidth: true
                                    text: name
                                    color: Theme.text
                                    font.pixelSize: Theme.fontSizeBodyLarge
                                    font.weight: Font.DemiBold
                                    elide: Text.ElideRight
                                }
                            }
                            Text {
                                Layout.fillWidth: true
                                text: path
                                color: Theme.textMuted
                                font.pixelSize: Theme.fontSizeCaption
                                elide: Text.ElideMiddle
                            }
                            Item { Layout.fillHeight: true }
                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 8
                                Rectangle {
                                    width: 7
                                    height: 7
                                    radius: 4
                                    color: needsAttention ? Theme.warning
                                        : runningTaskCount > 0 ? Theme.accentHover : Theme.success
                                }
                                Text {
                                    Layout.fillWidth: true
                                    text: projectStateText
                                    color: needsAttention ? Theme.warning : Theme.textMuted
                                    font.pixelSize: Theme.fontSizeCaption
                                    font.letterSpacing: projectStateText === "MEDIAFLOW PROJECT" ? 0.7 : 0
                                    elide: Text.ElideRight
                                }
                                AppButton {
                                    visible: recentArtifact.length > 0
                                    text: qsTr("最近产物")
                                    implicitHeight: 28
                                    leftPadding: 8
                                    rightPadding: 8
                                    font.pixelSize: Theme.fontSizeCaption
                                    onClicked: taskController.openArtifact(recentArtifact)
                                }
                                AppButton {
                                    objectName: "removeRecentProjectButton"
                                    property string projectPath: path
                                    text: qsTr("从列表移除")
                                    Accessible.name: text + " " + name
                                    implicitHeight: 28
                                    leftPadding: 8
                                    rightPadding: 8
                                    font.pixelSize: Theme.fontSizeCaption
                                    onClicked: workspaceController.removeRecentProject(path)
                                }
                            }
                        }
                    }
                }

                EmptyState {
                    objectName: "homeRecentEmptyState"
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    visible: recentList.count === 0
                    iconText: "▣"
                    title: qsTr("还没有最近项目")
                    description: qsTr("创建第一个项目后，下载、字幕、短视频和导出结果都会集中保存在项目目录中。")
                    contentMaximumWidth: 320
                    titleFontSize: Theme.fontSizeTitle
                    descriptionFontSize: Theme.fontSizeBodyLarge
                }
            }
        }

        Item { Layout.preferredHeight: 18 }
    }
}
}

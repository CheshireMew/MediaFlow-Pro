import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import ".."

Item {
    id: root
    signal exportRequested
    signal shortcutReferenceRequested
    property var workspaceItem: null
    readonly property string layoutPreset: workspaceItem
        ? String(workspaceItem.layoutPreset) : "standard"
    readonly property real statusControlWidth: Math.max(
        112, Math.min(statusText.implicitWidth, 220) + 34)

    ProjectVersionsDialog {
        id: projectVersionsDialog
        onOpened: {
            if (root.Window.window)
                root.Window.window.projectVersionsVisible = true;
        }
        onClosed: {
            if (root.Window.window)
                root.Window.window.projectVersionsVisible = false;
        }
    }

    implicitWidth: statusControlWidth
        + undoButton.implicitWidth
        + redoButton.implicitWidth
        + taskActivity.implicitWidth
        + layoutButton.implicitWidth
        + versionsButton.implicitWidth
        + closeProjectButton.implicitWidth
        + exportButton.implicitWidth
        + controls.spacing * 7
    implicitHeight: 42

    RowLayout {
        id: controls
        anchors.fill: parent
        spacing: 5

        Rectangle {
            Layout.minimumWidth: 112
            Layout.preferredWidth: root.statusControlWidth
            Layout.preferredHeight: 28
            radius: 14
            color: Theme.surfaceRaised
            border.color: Theme.borderSubtle

            Rectangle {
                id: statusSignal
                anchors.left: parent.left
                anchors.leftMargin: 11
                anchors.verticalCenter: parent.verticalCenter
                width: 7
                height: 7
                radius: 4
                color: mediaflow.workspaceViewController.readOnly ? Theme.warning : Theme.success
            }

            Text {
                id: statusText
                objectName: "workspaceStatusMessage"
                anchors.left: statusSignal.right
                anchors.leftMargin: 8
                anchors.right: parent.right
                anchors.rightMargin: 11
                anchors.verticalCenter: parent.verticalCenter
                text: mediaflow.workspaceViewController.readOnly
                    ? qsTr("只读")
                    : mediaflow.workspaceViewController.statusMessage || qsTr("已保存")
                color: mediaflow.workspaceViewController.readOnly ? Theme.warning : Theme.textSubtle
                font.pixelSize: Theme.fontSizeCaption
                elide: Text.ElideRight
                ToolTip.visible: statusHover.hovered && implicitWidth > width
                ToolTip.text: text
                HoverHandler {
                    id: statusHover
                }
            }
        }

        AppIconButton {
            id: undoButton
            objectName: "workspaceUndoButton"
            iconName: "undo"
            compact: true
            Accessible.name: qsTr("撤销")
            toolTipText: Accessible.name + " (Ctrl+Z)"
            enabled: mediaflow.workspaceViewController.actionCapabilities.canEdit
                && mediaflow.timelineViewController.canUndo
            onClicked: mediaflow.timelineStructureController.undo()
        }

        AppIconButton {
            id: redoButton
            objectName: "workspaceRedoButton"
            iconName: "redo"
            compact: true
            Accessible.name: qsTr("重做")
            toolTipText: Accessible.name + " (Ctrl+Y)"
            enabled: mediaflow.workspaceViewController.actionCapabilities.canEdit
                && mediaflow.timelineViewController.canRedo
            onClicked: mediaflow.timelineStructureController.redo()
        }

        Item {
            id: taskActivity
            objectName: "globalTaskActivity"
            Layout.preferredWidth: Theme.iconButtonSizeCompact
            Layout.preferredHeight: Theme.iconButtonSizeCompact
            implicitWidth: Theme.iconButtonSizeCompact
            implicitHeight: Theme.iconButtonSizeCompact
            AppIconButton {
                anchors.fill: parent
                iconName: "tasks"
                compact: true
                Accessible.name: mediaflow.workspaceViewController.recentErrors.length > 0
                    ? qsTr("任务中心，%1 条错误记录").arg(
                        mediaflow.workspaceViewController.recentErrors.length)
                    : mediaflow.taskController.activeTaskCount > 0
                    ? qsTr("任务中心，%1 个活动任务").arg(mediaflow.taskController.activeTaskCount)
                    : qsTr("任务中心")
                toolTipText: Accessible.name
                onClicked: mediaflow.taskController.openTaskCenter()
            }
            Rectangle {
                visible: mediaflow.taskController.activeTaskCount > 0
                    || mediaflow.workspaceViewController.recentErrors.length > 0
                anchors.right: parent.right
                anchors.top: parent.top
                width: Math.max(14, taskCount.implicitWidth + 6)
                height: 14
                radius: 7
                color: mediaflow.workspaceViewController.recentErrors.length > 0
                    ? Theme.danger
                    : mediaflow.taskController.pausedTaskCount > 0
                    ? Theme.warning : Theme.accent
                Text {
                    id: taskCount
                    anchors.centerIn: parent
                    text: mediaflow.workspaceViewController.recentErrors.length > 0
                        ? "!"
                        : mediaflow.taskController.activeTaskCount > 99
                        ? "99+" : String(mediaflow.taskController.activeTaskCount)
                    color: Theme.onAccent
                    font.pixelSize: Theme.fontSizeCaption
                    font.weight: Font.Bold
                }
            }
        }

        AppMenuButton {
            id: layoutButton
            objectName: "workspaceLayoutButton"
            text: root.layoutPreset === "media" ? qsTr("媒体布局")
                : root.layoutPreset === "vertical" ? qsTr("竖屏布局")
                : qsTr("标准布局")
            quiet: true
            implicitHeight: Theme.controlHeightCompact
            enabled: root.workspaceItem !== null

            AppMenu {
                id: layoutMenu
                y: layoutButton.height + 4

                AppMenuItem {
                    objectName: "workspaceLayoutStandard"
                    text: qsTr("标准布局") + "\tCtrl+Alt+1"
                    checkable: true
                    checked: root.layoutPreset === "standard"
                    onTriggered: root.workspaceItem.setWorkspaceLayoutPreset("standard")
                }
                AppMenuItem {
                    objectName: "workspaceLayoutMedia"
                    text: qsTr("媒体布局") + "\tCtrl+Alt+2"
                    checkable: true
                    checked: root.layoutPreset === "media"
                    onTriggered: root.workspaceItem.setWorkspaceLayoutPreset("media")
                }
                AppMenuItem {
                    objectName: "workspaceLayoutVertical"
                    text: qsTr("竖屏布局") + "\tCtrl+Alt+3"
                    checkable: true
                    checked: root.layoutPreset === "vertical"
                    onTriggered: root.workspaceItem.setWorkspaceLayoutPreset("vertical")
                }
                AppMenuSeparator {}
                AppMenuItem {
                    text: qsTr("界面导览")
                    onTriggered: mediaflow.workspaceProjectController.showWorkspaceTour()
                }
                AppMenuItem {
                    objectName: "workspaceShortcutReference"
                    text: qsTr("键盘快捷键") + "\tCtrl+/"
                    onTriggered: root.shortcutReferenceRequested()
                }
                AppMenuSeparator {}
                AppMenuItem {
                    objectName: "workspaceToggleTools"
                    text: qsTr("工具面板")
                    checkable: true
                    checked: Boolean(root.workspaceItem)
                        && Boolean(root.workspaceItem.toolPanelVisible)
                    onTriggered: root.workspaceItem.toggleWorkspacePanel("tool")
                }
                AppMenuItem {
                    objectName: "workspaceToggleInspector"
                    text: qsTr("检查器")
                    checkable: true
                    checked: Boolean(root.workspaceItem)
                        && Boolean(root.workspaceItem.inspectorPanelVisible)
                    onTriggered: root.workspaceItem.toggleWorkspacePanel("inspector")
                }
                AppMenuItem {
                    objectName: "workspaceToggleTimeline"
                    text: qsTr("时间线")
                    checkable: true
                    checked: Boolean(root.workspaceItem)
                        && Boolean(root.workspaceItem.timelinePanelVisible)
                    onTriggered: root.workspaceItem.toggleWorkspacePanel("timeline")
                }
                AppMenuSeparator {}
                AppMenuItem {
                    text: root.workspaceItem && root.workspaceItem.maximizedPanel === "preview"
                        ? qsTr("还原播放器") : qsTr("最大化播放器")
                    onTriggered: root.workspaceItem.togglePanelMaximized("preview")
                }
                AppMenuItem {
                    text: root.workspaceItem && root.workspaceItem.maximizedPanel === "tool"
                        ? qsTr("还原工具面板") : qsTr("最大化工具面板")
                    onTriggered: root.workspaceItem.togglePanelMaximized("tool")
                }
                AppMenuItem {
                    text: root.workspaceItem && root.workspaceItem.maximizedPanel === "inspector"
                        ? qsTr("还原检查器") : qsTr("最大化检查器")
                    onTriggered: root.workspaceItem.togglePanelMaximized("inspector")
                }
                AppMenuItem {
                    text: root.workspaceItem && root.workspaceItem.maximizedPanel === "timeline"
                        ? qsTr("还原时间线") : qsTr("最大化时间线")
                    onTriggered: root.workspaceItem.togglePanelMaximized("timeline")
                }
            }

            onClicked: layoutMenu.open()
        }

        AppIconButton {
            id: versionsButton
            objectName: "openProjectVersionsButton"
            iconName: "duplicate"
            compact: true
            Accessible.name: qsTr("版本")
            toolTipText: Accessible.name
            onClicked: projectVersionsDialog.open()
        }

        AppButton {
            id: closeProjectButton
            text: qsTr("关闭项目")
            quiet: true
            implicitHeight: Theme.controlHeightCompact
            enabled: mediaflow.workspaceViewController.actionCapabilities.canCloseProject
            onClicked: mediaflow.workspaceProjectController.closeProject()
        }

        AppButton {
            id: exportButton
            objectName: "titleExportButton"
            text: qsTr("导出")
            primary: true
            implicitHeight: Theme.controlHeightCompact
            enabled: mediaflow.workspaceViewController.actionCapabilities.canStartTasks
                && mediaflow.exportController.canExportSequence
            onClicked: root.exportRequested()
        }
    }
}

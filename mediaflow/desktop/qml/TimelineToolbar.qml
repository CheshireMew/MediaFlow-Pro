import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "."
import "components"

Rectangle {
    required property var view
    required property var timelineViewport
    Layout.fillWidth: true
    Layout.preferredHeight: 44
    color: Theme.surface
    border.color: Theme.transparent

    Rectangle {
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        height: 1
        color: Theme.divider
    }

    Flickable {
        id: toolbarFlick
        objectName: "timelineToolbarScroll"
        anchors.fill: parent
        clip: true
        contentWidth: toolbarRow.implicitWidth + 20
        contentHeight: height
        flickableDirection: Flickable.HorizontalFlick
        boundsBehavior: Flickable.StopAtBounds
        interactive: contentWidth > width
        ScrollBar.horizontal: AppScrollBar {
            policy: toolbarFlick.contentWidth > toolbarFlick.width ? ScrollBar.AlwaysOn : ScrollBar.AlwaysOff
        }

        RowLayout {
            id: toolbarRow
            x: 10
            height: toolbarFlick.height - 6
            spacing: 7
            SequenceToolbar {
                Layout.preferredWidth: implicitWidth
                Layout.preferredHeight: implicitHeight
                actionsEnabled: view.canEdit
                onCreateShortRequested: {
                    if (view.canEdit)
                        workspaceController.createShortSequence("");
                }
                onEditProfileRequested: view.editProfileRequested()
            }
            AppButton {
                objectName: "timelineMultiSelectButton"
                text: qsTr("多选")
                checkable: true
                checked: view.multiSelectMode
                quiet: !checked
                onToggled: view.multiSelectMode = checked
                ToolTip.visible: hovered
                ToolTip.text: qsTr("开启后直接点击多个片段即可选择；不需要按快捷键")
            }
            Text {
                visible: timelineController.selectedClipIds.length > 0
                text: timelineController.selectedCompoundId.length > 0 ? qsTr("已选复合片段") : qsTr("已选 %1 个").arg(timelineController.selectedClipIds.length)
                color: Theme.accentHover
                font.pixelSize: Theme.fontSizeBodySmall
                font.weight: Font.DemiBold
            }
            AppButton {
                objectName: "clearTimelineSelectionButton"
                text: qsTr("清除选择")
                quiet: true
                visible: timelineController.selectedClipIds.length > 0
                onClicked: view.clearTimelineSelection()
            }
            AppButton {
                objectName: "createCompoundClipButton"
                text: qsTr("创建复合片段")
                quiet: true
                enabled: view.canEdit && timelineController.canCreateCompoundClip
                visible: timelineController.selectedCompoundId.length === 0
                onClicked: timelineController.createCompoundClip()
                ToolTip.visible: hovered
                ToolTip.text: qsTr("把同一轨道上首尾相接的所选片段合成一个整体")
            }
            AppButton {
                objectName: "dissolveCompoundClipButton"
                text: qsTr("解除复合")
                quiet: true
                visible: timelineController.selectedCompoundId.length > 0
                enabled: view.canEdit
                onClicked: timelineController.dissolveSelectedCompoundClip()
            }
            AppIconButton {
                iconName: "cut"
                flat: true
                Accessible.name: qsTr("分割片段")
                enabled: view.canEdit && timelineController.selectedClipId.length > 0
                onClicked: timelineController.splitClip(timelineController.selectedClipId, view.playheadFrame)
                toolTipText: qsTr("在播放头处分割所选片段（Ctrl+K / Ctrl+B）")
            }
            AppIconButton {
                iconName: "duplicate"
                flat: true
                Accessible.name: qsTr("创建片段副本")
                enabled: view.canEdit && timelineController.selectedClipId.length > 0
                onClicked: timelineController.duplicateClip(timelineController.selectedClipId, view.pixelsPerFrame, view.playheadFrame)
                toolTipText: qsTr("在片段末尾创建副本（Ctrl+D）")
            }
            AppIconButton {
                iconName: "delete"
                flat: true
                danger: true
                Accessible.name: qsTr("删除所选片段")
                enabled: view.canEdit && timelineController.selectedClipIds.length > 0
                onClicked: timelineController.deleteSelectedClips(false)
                toolTipText: qsTr("删除所选片段并保留空隙（Delete）")
            }
            AppButton {
                text: qsTr("波纹删")
                quiet: true
                enabled: view.canEdit && timelineController.selectedClipIds.length > 0
                onClicked: timelineController.deleteSelectedClips(true)
                ToolTip.visible: hovered
                ToolTip.text: qsTr("删除所选片段并闭合空隙（Shift+Delete）")
            }
            AppButton {
                text: "M"
                quiet: true
                Accessible.name: qsTr("添加标记")
                leftPadding: 11
                rightPadding: 11
                enabled: view.canEdit
                onClicked: timelineController.addTimelineMarker(view.playheadFrame)
                ToolTip.visible: hovered
                ToolTip.text: qsTr("在播放头处添加标记（M）")
            }
            AppButton {
                text: "I"
                quiet: true
                Accessible.name: qsTr("设置入点")
                leftPadding: 11
                rightPadding: 11
                enabled: view.canEdit && workspaceController.timelineDurationFrames > 0
                onClicked: timelineController.setSequenceInPoint(view.playheadFrame)
                ToolTip.visible: hovered
                ToolTip.text: qsTr("设置入点（I）")
            }
            AppButton {
                text: "O"
                quiet: true
                Accessible.name: qsTr("设置出点")
                leftPadding: 11
                rightPadding: 11
                enabled: view.canEdit && workspaceController.timelineDurationFrames > 0
                onClicked: timelineController.setSequenceOutPoint(view.playheadFrame)
                ToolTip.visible: hovered
                ToolTip.text: qsTr("设置出点（O）")
            }
            AppButton {
                objectName: "timelineSnapButton"
                text: qsTr("吸附")
                checkable: true
                checked: view.snapEnabled
                quiet: !checked
                onClicked: view.snapEnabled = checked
                ToolTip.visible: hovered
                ToolTip.text: checked ? qsTr("吸附已开启（S）") : qsTr("吸附已关闭（S）")
            }
            AppMenuButton {
                id: timelineMoreButton
                objectName: "timelineMoreButton"
                text: qsTr("更多")
                quiet: true
                onClicked: timelineMoreMenu.open()
                AppMenu {
                    id: timelineMoreMenu
                    y: timelineMoreButton.height + 4
                    AppMenuItem {
                        objectName: "smartSequenceBoundsButton"
                        text: timelineController.sequenceBoundaryAnalysisRunning ? qsTr("正在分析入出点…") : qsTr("智能设置入出点")
                        enabled: view.canEdit && workspaceController.timelineDurationFrames > 0 && !timelineController.sequenceBoundaryAnalysisRunning
                        onTriggered: timelineController.analyzeSequenceBoundaries()
                    }
                    AppMenuItem {
                        text: qsTr("清除入点和出点")
                        enabled: view.canEdit && workspaceController.hasSequenceInOut
                        onTriggered: timelineController.clearSequenceInOut()
                    }
                    AppMenuSeparator {}
                    AppMenuItem {
                        text: timelineController.rangeInFrame < 0 ? qsTr("设置短视频选区起点") : qsTr("重新设置短视频选区起点")
                        enabled: view.canEdit
                        onTriggered: timelineController.setRangeIn(view.playheadFrame)
                    }
                    AppMenuItem {
                        text: qsTr("设置短视频选区终点")
                        enabled: view.canEdit && timelineController.rangeInFrame >= 0
                        onTriggered: timelineController.commitTimelineRange(view.playheadFrame)
                    }
                    AppMenuItem {
                        text: qsTr("从所选区间创建短视频")
                        enabled: view.canEdit && timelineController.selectedRangeId.length > 0
                        onTriggered: timelineController.createShortFromRange(timelineController.selectedRangeId)
                    }
                    AppMenuSeparator {}
                    AppMenuItem {
                        text: qsTr("添加视频轨")
                        enabled: view.canEdit
                        onTriggered: timelineController.addTrack("video")
                    }
                    AppMenuItem {
                        text: qsTr("添加音频轨")
                        enabled: view.canEdit
                        onTriggered: timelineController.addTrack("audio")
                    }
                    AppMenuItem {
                        text: qsTr("添加字幕轨")
                        enabled: view.canEdit
                        onTriggered: timelineController.addTrack("subtitle")
                    }
                }
            }
            Item {
                Layout.fillWidth: true
            }
            Text {
                text: qsTr("缩放")
                color: Theme.textMuted
                font.pixelSize: Theme.fontSizeCaption
            }
            AppSlider {
                id: timelineZoomSlider
                objectName: "timelineZoomSlider"
                from: view.minimumPixelsPerFrame
                to: 12
                value: view.pixelsPerFrame
                onMoved: {
                    let anchorFrame = view.visiblePlayheadFrame;
                    let anchorX = anchorFrame * view.pixelsPerFrame - timelineViewport.contentX;
                    if (anchorX < 0 || anchorX > timelineViewport.width) {
                        anchorX = timelineViewport.width / 2;
                        anchorFrame = (timelineViewport.contentX + anchorX) / view.pixelsPerFrame;
                    }
                    view.setTimelineZoom(value, anchorFrame, anchorX);
                }
                Layout.preferredWidth: 110
            }
            AppButton {
                objectName: "fitTimelineButton"
                text: qsTr("适配")
                quiet: true
                Accessible.name: qsTr("适配整个时间线")
                onClicked: view.fitTimeline()
                ToolTip.visible: hovered
                ToolTip.text: qsTr("显示整条时间线")
            }
        }
    }
}

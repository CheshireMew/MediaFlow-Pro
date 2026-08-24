import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import ".."

ListView {
    id: segmentList
    objectName: "subtitleSegmentList"
    property bool canEdit: false
    signal seekRequested(int frame)

    function formatTimecode(frame) {
        const numerator = Math.max(1, Number(mediaflow.workspaceViewController.profileFpsNumerator || 30));
        const denominator = Math.max(1, Number(mediaflow.workspaceViewController.profileFpsDenominator || 1));
        const nominalFps = Math.max(1, Math.round(numerator / denominator));
        const value = Math.max(0, Number(frame));
        const totalSeconds = Math.floor(value / nominalFps);
        const frames = Math.floor(value % nominalFps);
        const seconds = totalSeconds % 60;
        const minutes = Math.floor(totalSeconds / 60) % 60;
        const hours = Math.floor(totalSeconds / 3600);
        function pad(number) {
            return String(number).padStart(2, "0");
        }
        return pad(hours) + ":" + pad(minutes) + ":" + pad(seconds) + ":" + pad(frames);
    }

    Connections {
        target: mediaflow.subtitleViewController
        function onSelectionChanged() {
            const row = mediaflow.subtitleViewController.subtitleSegmentsModel.findRow("segmentId", mediaflow.subtitleViewController.selectedSubtitleSegmentId);
            if (row >= 0)
                segmentList.positionViewAtIndex(row, ListView.Contain);
        }
    }
    Layout.fillWidth: true
    Layout.preferredHeight: Math.max(160, Math.min(360, contentHeight))
    clip: true
    spacing: 5
    model: mediaflow.subtitleViewController.subtitleSegmentsModel
    delegate: Rectangle {
        required property string segmentId
        required property int startFrame
        required property int endFrame
        required property string text
        required property bool hasOverlap
        width: segmentList.width
        height: segmentPreviewText.implicitHeight + 31
        radius: Theme.radiusSmall
        color: mediaflow.subtitleViewController.isSubtitleSegmentSelected(segmentId) ? Theme.accentSoft : segmentMouse.containsMouse ? Theme.surfaceHover : Theme.surfaceRaised
        border.color: hasOverlap ? Theme.danger : mediaflow.subtitleViewController.isSubtitleSegmentSelected(segmentId) ? Theme.accent : Theme.border
        ColumnLayout {
            anchors.fill: parent
            anchors.margins: 8
            spacing: 3
            Text {
                text: segmentList.formatTimecode(startFrame) + " – " + segmentList.formatTimecode(endFrame)
                color: hasOverlap ? Theme.danger : Theme.textMuted
                font.pixelSize: Theme.fontSizeCaption
                font.family: Theme.monoFontFamily
            }
            Text {
                id: segmentPreviewText
                Layout.fillWidth: true
                text: parent.parent.text
                color: Theme.text
                font.pixelSize: Theme.fontSizeCaption
                wrapMode: Text.WordWrap
            }
        }
        MouseArea {
            id: segmentMouse
            anchors.fill: parent
            hoverEnabled: true
            acceptedButtons: Qt.LeftButton | Qt.RightButton
            onClicked: function (mouse) {
                if (mouse.button === Qt.RightButton) {
                    if (!mediaflow.subtitleViewController.isSubtitleSegmentSelected(segmentId))
                        mediaflow.subtitleViewController.selectSubtitleSegment(segmentId, false);
                    segmentContextMenu.popup();
                    return;
                }
                mediaflow.subtitleViewController.selectSubtitleSegment(segmentId, (mouse.modifiers & Qt.ControlModifier) !== 0);
                segmentList.seekRequested(mediaflow.subtitleViewController.subtitleSegmentTimelineFrame(segmentId, startFrame));
            }
            onDoubleClicked: mediaflow.subtitleViewController.previewSubtitleSegment(segmentId)
        }
        AppMenu {
            id: segmentContextMenu
            AppMenuItem {
                text: qsTr("播放这一条")
                onTriggered: mediaflow.subtitleViewController.previewSubtitleSegment(segmentId)
            }
            AppMenuSeparator {}
            AppMenuItem {
                text: qsTr("翻译所选字幕")
                enabled: segmentList.canEdit
                onTriggered: mediaflow.subtitleTranslationController.translateSelectedSubtitleSegments()
            }
            AppMenuItem {
                text: qsTr("复制所选字幕")
                onTriggered: mediaflow.subtitleEditingController.copySelectedSubtitleSegments()
            }
            AppMenuItem {
                text: qsTr("合并所选字幕")
                enabled: segmentList.canEdit && mediaflow.subtitleViewController.selectedSubtitleSegmentIds.length > 1
                onTriggered: mediaflow.subtitleEditingController.mergeSelectedSubtitleSegments()
            }
            AppMenuItem {
                text: qsTr("按中点拆分")
                enabled: segmentList.canEdit && mediaflow.subtitleViewController.selectedSubtitleSegmentIds.length === 1
                onTriggered: mediaflow.subtitleEditingController.splitSubtitleSegment(segmentId, -1)
            }
            AppMenuSeparator {}
            AppMenuItem {
                text: qsTr("删除所选字幕")
                enabled: segmentList.canEdit
                onTriggered: mediaflow.subtitleEditingController.deleteSelectedSubtitleSegments()
            }
        }
    }
    EmptyState {
        anchors.fill: parent
        visible: segmentList.count === 0
        iconName: "subtitle"
        title: qsTr("还没有字幕")
        description: qsTr("转录媒体或导入 SRT 后，可以在这里逐条编辑。")
    }
}

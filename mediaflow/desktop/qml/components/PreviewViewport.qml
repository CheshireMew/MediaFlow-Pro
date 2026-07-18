import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Window
import MediaFlow.Native 1.0
import ".."

Rectangle {
    id: root
    objectName: "previewViewport"
    color: "#08090b"
    border.color: Theme.border
    clip: true

    property int sequenceIn: 0
    property int sequenceOut: Math.max(sequenceIn + 1, preview.duration)
    property string source: ""
    property string runtimeRoot: ""
    property int reloadToken: 0
    property bool hdrEnabled: false
    property int profileWidth: 1920
    property int profileHeight: 1080
    property string subtitleText: ""
    readonly property int position: preview.position
    readonly property int duration: preview.duration
    readonly property bool playing: preview.playing
    property alias playbackRate: preview.playbackRate

    property int rangeEnd: -1
    property real viewportZoom: 1.0
    property real viewportPanX: 0
    property real viewportPanY: 0
    property real previewVolume: 1.0
    property real previewVolumeBeforeMute: 1.0
    property bool previewMuted: false
    property bool resumeAfterScrub: false

    signal droppedFramesReported(int droppedFrames)
    signal hdrActiveReported(bool active)

    function toggleMute() {
        if (previewMuted) {
            previewMuted = false;
            previewVolume = Math.max(0.01, previewVolumeBeforeMute);
        } else {
            previewVolumeBeforeMute = Math.max(0.01, previewVolume);
            previewMuted = true;
        }
    }

    function toggleFullscreen() {
        const host = root.Window.window;
        if (!host)
            return;
        host.visibility = host.visibility === Window.FullScreen ? Window.Windowed : Window.FullScreen;
    }

    function playPreview() {
        const end = rangeEnd >= 0 ? rangeEnd : sequenceOut;
        const lastFrame = Math.max(sequenceIn, end - 1);
        if (preview.position < sequenceIn || preview.position >= lastFrame)
            preview.seek(sequenceIn);
        preview.playRange(sequenceIn, end);
    }

    function playReversePreview() {
        const end = rangeEnd >= 0 ? rangeEnd : sequenceOut;
        const lastFrame = Math.max(sequenceIn, end - 1);
        if (preview.position <= sequenceIn || preview.position > lastFrame)
            preview.seek(lastFrame);
        preview.playRange(sequenceIn, end);
    }

    function playRequestedRange(startFrame, endFrame) {
        rangeEnd = endFrame;
        preview.seek(startFrame);
        preview.playRange(startFrame, endFrame);
    }

    function stopPreview() {
        preview.pause();
        rangeEnd = -1;
        preview.seek(sequenceIn);
    }

    function beginScrub() {
        resumeAfterScrub = preview.playing;
        preview.pause();
    }

    function endScrub() {
        if (!resumeAfterScrub)
            return;
        resumeAfterScrub = false;
        playPreview();
    }

    function resetViewport() {
        viewportZoom = 1.0;
        viewportPanX = 0;
        viewportPanY = 0;
    }

    function seek(frame) {
        preview.seek(frame);
    }

    function pause() {
        preview.pause();
    }

    DragHandler {
        id: previewPanHandler
        target: null
        acceptedButtons: Qt.MiddleButton
        property real startX: 0
        property real startY: 0
        onActiveChanged: {
            if (active) {
                startX = root.viewportPanX;
                startY = root.viewportPanY;
            }
        }
        onTranslationChanged: {
            if (active) {
                root.viewportPanX = startX + translation.x;
                root.viewportPanY = startY + translation.y;
            }
        }
    }

    WheelHandler {
        onWheel: function (event) {
            var factor = event.angleDelta.y > 0 ? 1.12 : 1 / 1.12;
            root.viewportZoom = Math.max(0.5, Math.min(4.0, root.viewportZoom * factor));
            event.accepted = true;
        }
    }

    Rectangle {
        id: previewSurface
        objectName: "previewSurface"
        anchors.centerIn: parent
        readonly property real targetAspectRatio: Math.max(1, root.profileWidth) / Math.max(1, root.profileHeight)
        readonly property real maximumWidth: Math.max(1, Math.min(parent.width - 80, 820))
        readonly property real maximumHeight: Math.max(1, parent.height - 92)
        width: Math.min(maximumWidth, maximumHeight * targetAspectRatio)
        height: width / targetAspectRatio
        scale: root.viewportZoom
        transform: Translate {
            x: root.viewportPanX
            y: root.viewportPanY
        }
        color: "#020304"
        border.color: Theme.borderStrong

        MltPreviewItem {
            id: preview
            objectName: "previewPlayer"
            anchors.fill: parent
            anchors.margins: 1
            source: root.source
            runtimeRoot: root.runtimeRoot
            reloadToken: root.reloadToken
            hdrEnabled: root.hdrEnabled
            volume: root.previewMuted ? 0.0 : root.previewVolume
            onDroppedFramesChanged: root.droppedFramesReported(droppedFrames)
            onHdrActiveChanged: root.hdrActiveReported(hdrActive)
            onPositionChanged: {
                const end = root.rangeEnd >= 0 ? root.rangeEnd : root.sequenceOut;
                const lastFrame = Math.max(root.sequenceIn, end - 1);
                if (playbackRate >= 0 && position >= lastFrame) {
                    pause();
                    if (root.rangeEnd >= 0)
                        root.rangeEnd = -1;
                } else if (playbackRate < 0 && position <= root.sequenceIn) {
                    pause();
                }
            }
        }

        PreviewTransformOverlay {
            anchors.fill: parent
            previewPosition: preview.position
        }

        Text {
            anchors.centerIn: parent
            visible: root.source.length === 0 || preview.errorString.length > 0
            text: preview.errorString.length > 0 ? qsTr("预览不可用：") + preview.errorString : qsTr("把素材添加到时间线开始创作")
            color: Theme.textMuted
            font.pixelSize: Theme.fontSizeBodyLarge
            wrapMode: Text.WordWrap
            horizontalAlignment: Text.AlignHCenter
            width: Math.min(parent.width - 40, 520)
        }

        Text {
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.bottom: parent.bottom
            anchors.leftMargin: 28
            anchors.rightMargin: 28
            anchors.bottomMargin: 18
            text: root.subtitleText
            visible: text.length > 0
            color: "white"
            font.pixelSize: Math.max(18, previewSurface.height * 0.055)
            font.weight: Font.DemiBold
            style: Text.Outline
            styleColor: "black"
            wrapMode: Text.WordWrap
            horizontalAlignment: Text.AlignHCenter
        }

        Rectangle {
            anchors.top: parent.top
            anchors.right: parent.right
            anchors.margins: 8
            width: hdrPreviewLabel.implicitWidth + 14
            height: 24
            radius: 12
            color: "#7a4a18"
            visible: root.hdrEnabled
            Text {
                id: hdrPreviewLabel
                anchors.centerIn: parent
                text: preview.hdrActive ? qsTr("HDR 预览") : qsTr("HDR 项目 / SDR 预览")
                color: "white"
                font.pixelSize: Theme.fontSizeCaption
            }
        }
    }

    RowLayout {
        anchors.bottom: parent.bottom
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.bottomMargin: 12
        spacing: 10

        AppButton {
            text: "◀"
            Accessible.name: qsTr("上一帧")
            enabled: preview.duration > 0
            onClicked: preview.seek(Math.max(root.sequenceIn, preview.position - 1))
        }
        AppButton {
            text: preview.playing ? "Ⅱ" : "▶"
            Accessible.name: preview.playing ? qsTr("暂停") : qsTr("播放")
            primary: true
            enabled: preview.duration > 0
            onClicked: preview.playing ? preview.pause() : root.playPreview()
        }
        AppButton {
            text: "■"
            Accessible.name: qsTr("停止并回到入点")
            enabled: preview.duration > 0
            onClicked: root.stopPreview()
        }
        Text {
            text: preview.position + " / " + preview.duration
            color: Theme.textMuted
            font.family: Theme.monoFontFamily
            font.pixelSize: Theme.fontSizeCaption
        }
        Slider {
            objectName: "previewPositionSlider"
            Layout.preferredWidth: 140
            from: root.sequenceIn
            to: Math.max(root.sequenceIn, root.sequenceOut - 1)
            value: preview.position
            enabled: preview.duration > 0
            onPressedChanged: pressed ? root.beginScrub() : root.endScrub()
            onMoved: preview.seek(Math.round(value))
            Accessible.name: qsTr("播放位置")
        }
        AppComboBox {
            Layout.preferredWidth: 76
            textRole: "label"
            valueRole: "value"
            model: [
                { label: "1×", value: 1.0 },
                { label: "1.25×", value: 1.25 },
                { label: "1.5×", value: 1.5 },
                { label: "1.75×", value: 1.75 },
                { label: "2×", value: 2.0 },
                { label: "2.5×", value: 2.5 },
                { label: "3×", value: 3.0 }
            ]
            onActivated: preview.playbackRate = Number(currentValue)
            Accessible.name: qsTr("播放速度")
        }
        AppButton {
            text: root.previewMuted || root.previewVolume <= 0 ? "🔇" : "🔊"
            Accessible.name: root.previewMuted ? qsTr("取消静音") : qsTr("静音")
            onClicked: root.toggleMute()
        }
        Slider {
            Layout.preferredWidth: 82
            from: 0
            to: 1
            stepSize: 0.01
            value: root.previewMuted ? 0 : root.previewVolume
            onMoved: {
                root.previewVolume = value;
                root.previewMuted = value <= 0;
                if (value > 0)
                    root.previewVolumeBeforeMute = value;
            }
            Accessible.name: qsTr("预览音量")
        }
        Text {
            visible: preview.droppedFrames > 0
            text: qsTr("掉帧 ") + preview.droppedFrames
            color: Theme.warning
            font.pixelSize: Theme.fontSizeCaption
        }
        AppButton {
            text: "−"
            Accessible.name: qsTr("缩小预览")
            onClicked: root.viewportZoom = Math.max(0.5, root.viewportZoom / 1.2)
        }
        AppButton {
            objectName: "previewZoomReset"
            text: Math.round(root.viewportZoom * 100) + "%"
            Accessible.name: qsTr("重置预览视图")
            onClicked: root.resetViewport()
        }
        AppButton {
            text: "+"
            Accessible.name: qsTr("放大预览")
            onClicked: root.viewportZoom = Math.min(4, root.viewportZoom * 1.2)
        }
        AppButton {
            text: "⛶"
            Accessible.name: qsTr("切换全屏")
            onClicked: root.toggleFullscreen()
        }
    }
}

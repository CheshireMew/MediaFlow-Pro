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

    property string source: ""
    property string runtimeRoot: ""
    property bool hdrEnabled: false
    property int profileWidth: 1920
    property int profileHeight: 1080
    property string subtitleText: ""
    property bool exportPreviewActive: false
    property var exportPreviewOptions: ({})
    property url watermarkSource: ""
    readonly property var subtitlePreviewStyle: exportPreviewOptions.subtitleStyle || ({})
    readonly property var watermarkPreview: exportPreviewOptions.watermark || ({})
    readonly property int position: preview.position
    readonly property int duration: preview.duration
    readonly property bool playing: preview.playing
    readonly property bool playbackRequested: pendingPlaybackMode !== 0
    property alias playbackRate: preview.playbackRate

    property int playbackRangeStart: -1
    property int playbackRangeEnd: -1
    property int pendingPlaybackMode: 0
    property int pendingPlaybackFrame: 0
    property int pendingPlaybackAttempts: 0
    property real viewportZoom: 1.0
    property real viewportPanX: 0
    property real viewportPanY: 0
    property real previewVolume: 1.0
    property real previewVolumeBeforeMute: 1.0
    property bool previewMuted: false
    property bool resumeAfterScrub: false
    property int scrubFrame: 0

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

    function boundedPlaybackFrame(frame) {
        return Math.max(0, Math.min(Math.max(0, preview.duration - 1), Math.round(frame)));
    }

    function clearPlaybackRange() {
        playbackRangeStart = -1;
        playbackRangeEnd = -1;
    }

    function cancelPendingPlayback() {
        pendingPlaybackMode = 0;
        pendingPlaybackAttempts = 0;
        playbackRetryTimer.stop();
    }

    function attemptPendingPlayback() {
        if (pendingPlaybackMode === 0)
            return;
        if (pendingPlaybackAttempts >= 40) {
            cancelPendingPlayback();
            return;
        }
        pendingPlaybackAttempts += 1;
        if (preview.duration <= 0) {
            playbackRetryTimer.start();
            return;
        }
        if (pendingPlaybackMode === 2) {
            playbackRangeStart = Math.max(
                0,
                Math.min(preview.duration - 1, playbackRangeStart));
            playbackRangeEnd = Math.max(
                playbackRangeStart + 1,
                Math.min(preview.duration, playbackRangeEnd));
        }
        const startFrame = boundedPlaybackFrame(pendingPlaybackFrame);
        if (pendingPlaybackMode > 0 && preview.playbackRate <= 0)
            preview.playbackRate = 1.0;
        else if (pendingPlaybackMode < 0 && preview.playbackRate >= 0)
            preview.playbackRate = -1.0;
        if ((pendingPlaybackMode === 1 && startFrame >= preview.duration - 1)
                || (pendingPlaybackMode === -1 && startFrame <= 0)) {
            cancelPendingPlayback();
            preview.seek(startFrame);
            preview.pause();
            return;
        }
        preview.seek(startFrame);
        if (pendingPlaybackMode === 2)
            preview.playRange(playbackRangeStart, playbackRangeEnd);
        else
            preview.playRange(0, preview.duration);
        playbackRetryTimer.start();
    }

    function playPreviewFrom(frame) {
        clearPlaybackRange();
        pendingPlaybackMode = 1;
        pendingPlaybackFrame = Math.max(0, Math.round(frame));
        pendingPlaybackAttempts = 0;
        attemptPendingPlayback();
    }

    function playPreview() {
        playPreviewFrom(preview.position);
    }

    function playReversePreviewFrom(frame) {
        clearPlaybackRange();
        pendingPlaybackMode = -1;
        pendingPlaybackFrame = Math.max(0, Math.round(frame));
        pendingPlaybackAttempts = 0;
        attemptPendingPlayback();
    }

    function playReversePreview() {
        playReversePreviewFrom(preview.position);
    }

    function playRequestedRange(startFrame, endFrame) {
        playbackRangeStart = Math.max(0, Math.round(startFrame));
        playbackRangeEnd = Math.max(
            playbackRangeStart + 1,
            Math.round(endFrame));
        pendingPlaybackMode = 2;
        pendingPlaybackFrame = playbackRangeStart;
        pendingPlaybackAttempts = 0;
        attemptPendingPlayback();
    }

    function stopPreview() {
        cancelPendingPlayback();
        preview.pause();
        clearPlaybackRange();
        preview.seek(0);
    }

    function beginScrub() {
        cancelPendingPlayback();
        scrubFrame = preview.position;
        resumeAfterScrub = preview.playing;
        preview.pause();
    }

    function endScrub() {
        if (!resumeAfterScrub)
            return;
        resumeAfterScrub = false;
        if (playbackRangeStart >= 0 && playbackRangeEnd > playbackRangeStart) {
            pendingPlaybackFrame = Math.max(
                playbackRangeStart,
                Math.min(playbackRangeEnd - 1, scrubFrame));
            pendingPlaybackMode = 2;
            pendingPlaybackAttempts = 0;
            attemptPendingPlayback();
        } else {
            playPreviewFrom(scrubFrame);
        }
    }

    function resetViewport() {
        viewportZoom = 1.0;
        viewportPanX = 0;
        viewportPanY = 0;
    }

    function seek(frame) {
        scrubFrame = boundedPlaybackFrame(frame);
        preview.seek(scrubFrame);
    }

    function pause() {
        cancelPendingPlayback();
        preview.pause();
    }

    Timer {
        id: playbackRetryTimer
        interval: 100
        repeat: false
        onTriggered: root.attemptPendingPlayback()
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
            hdrEnabled: root.hdrEnabled
            volume: root.previewMuted ? 0.0 : root.previewVolume
            onDroppedFramesChanged: root.droppedFramesReported(droppedFrames)
            onHdrActiveChanged: root.hdrActiveReported(hdrActive)
            onDurationChanged: if (preview.duration > 0 && root.playbackRequested)
                Qt.callLater(root.attemptPendingPlayback)
            onPlayingChanged: if (preview.playing)
                root.cancelPendingPlayback()
            onPositionChanged: {
                const start = root.playbackRangeStart >= 0 ? root.playbackRangeStart : 0;
                const end = root.playbackRangeEnd > start ? root.playbackRangeEnd : preview.duration;
                const lastFrame = Math.max(start, end - 1);
                if (preview.playbackRate >= 0 && preview.position >= lastFrame) {
                    preview.pause();
                    if (root.playbackRangeStart >= 0)
                        root.clearPlaybackRange();
                } else if (preview.playbackRate < 0 && preview.position <= start) {
                    preview.pause();
                }
            }
        }

        PreviewTransformOverlay {
            anchors.fill: parent
            previewPosition: preview.position
            interactionVisible: !root.exportPreviewActive
        }

        Image {
            id: exportWatermarkPreview
            objectName: "exportWatermarkPreview"
            readonly property real widthRatio: Number(root.watermarkPreview.width_ratio || 0.2)
            readonly property real heightRatio: Math.min(
                1.0,
                previewSurface.width * widthRatio * Math.max(1, implicitHeight)
                    / (Math.max(1, implicitWidth) * previewSurface.height))
            readonly property real marginX: root.profileHeight > root.profileWidth ? 0.045 : 0.03
            readonly property real marginY: root.profileHeight > root.profileWidth ? 0.035 : 0.05
            readonly property string placement: String(root.watermarkPreview.position || "TR")
            readonly property real centerXRatio: root.watermarkPreview.position_x !== null
                    && root.watermarkPreview.position_x !== undefined
                ? Number(root.watermarkPreview.position_x)
                : placement.indexOf("L") >= 0 ? marginX + widthRatio / 2
                : placement.indexOf("R") >= 0 ? 1 - marginX - widthRatio / 2 : 0.5
            readonly property real centerYRatio: root.watermarkPreview.position_y !== null
                    && root.watermarkPreview.position_y !== undefined
                ? Number(root.watermarkPreview.position_y)
                : placement.indexOf("T") >= 0 ? marginY + heightRatio / 2
                : placement.indexOf("B") >= 0 ? 1 - marginY - heightRatio / 2 : 0.5
            visible: root.exportPreviewActive && source.toString().length > 0
                && Boolean(root.watermarkPreview.enabled)
            source: root.watermarkSource
            width: previewSurface.width * widthRatio
            height: previewSurface.height * heightRatio
            x: Math.max(0, Math.min(previewSurface.width - width,
                previewSurface.width * centerXRatio - width / 2))
            y: Math.max(0, Math.min(previewSurface.height - height,
                previewSurface.height * centerYRatio - height / 2))
            opacity: Number(root.watermarkPreview.opacity ?? 1)
            fillMode: Image.Stretch
            smooth: true
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

        Item {
            id: subtitlePreviewGeometry
            objectName: "exportSubtitlePreview"
            readonly property bool styled: root.exportPreviewActive
            readonly property real positionX: Number(root.subtitlePreviewStyle.position_x ?? 0.5)
            readonly property real positionY: Number(root.subtitlePreviewStyle.position_y ?? 0.88)
            width: parent.width * 0.9
            height: parent.height * 0.25
            x: Math.max(0, Math.min(parent.width - width, parent.width * positionX - width / 2))
            y: Math.max(0, Math.min(parent.height - height, parent.height * positionY - height / 2))
            visible: root.subtitleText.length > 0

            Rectangle {
                anchors.fill: subtitlePreviewText
                anchors.margins: -Math.max(0, Number(root.subtitlePreviewStyle.background_padding || 0))
                    * previewSurface.height / 540
                visible: subtitlePreviewGeometry.styled
                    && Boolean(root.subtitlePreviewStyle.background_enabled)
                color: root.subtitlePreviewStyle.background_color || "#000000"
                opacity: Number(root.subtitlePreviewStyle.background_opacity || 0)
                radius: 2
            }
            Text {
                id: subtitlePreviewText
                objectName: "exportSubtitlePreviewText"
                anchors.fill: parent
                text: root.subtitleText
                color: subtitlePreviewGeometry.styled
                    ? root.subtitlePreviewStyle.font_color || "#FFFFFF" : "white"
                font.family: subtitlePreviewGeometry.styled
                    ? root.subtitlePreviewStyle.font_family || "Microsoft YaHei UI" : "Microsoft YaHei UI"
                font.pixelSize: subtitlePreviewGeometry.styled
                    ? Math.max(8, Number(root.subtitlePreviewStyle.font_size || 24)
                        * previewSurface.height / 540)
                    : Math.max(18, previewSurface.height * 0.055)
                font.weight: subtitlePreviewGeometry.styled
                    && Boolean(root.subtitlePreviewStyle.bold) ? Font.Bold : Font.DemiBold
                font.italic: subtitlePreviewGeometry.styled
                    && Boolean(root.subtitlePreviewStyle.italic)
                style: Text.Outline
                styleColor: subtitlePreviewGeometry.styled
                    ? root.subtitlePreviewStyle.outline_color || "#000000" : "black"
                wrapMode: Text.WordWrap
                horizontalAlignment: !subtitlePreviewGeometry.styled
                    || root.subtitlePreviewStyle.alignment === "center" ? Text.AlignHCenter
                    : root.subtitlePreviewStyle.alignment === "right" ? Text.AlignRight : Text.AlignLeft
                verticalAlignment: !subtitlePreviewGeometry.styled
                    || root.subtitlePreviewStyle.multiline_alignment === "center" ? Text.AlignVCenter
                    : root.subtitlePreviewStyle.multiline_alignment === "bottom"
                    ? Text.AlignBottom : Text.AlignTop
            }
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
            ToolTip.visible: hovered
            ToolTip.text: Accessible.name
            enabled: preview.duration > 0
            onClicked: preview.seek(Math.max(0, preview.position - 1))
        }
        AppButton {
            text: preview.playing ? "Ⅱ" : "▶"
            Accessible.name: preview.playing ? qsTr("暂停") : qsTr("播放")
            ToolTip.visible: hovered
            ToolTip.text: Accessible.name + qsTr("（空格）")
            primary: true
            enabled: preview.duration > 0
            onClicked: preview.playing || root.playbackRequested ? root.pause() : root.playPreview()
        }
        AppButton {
            text: "■"
            Accessible.name: qsTr("停止并回到开头")
            ToolTip.visible: hovered
            ToolTip.text: Accessible.name
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
            from: 0
            to: Math.max(0, preview.duration - 1)
            value: preview.position
            enabled: preview.duration > 0
            onPressedChanged: pressed ? root.beginScrub() : root.endScrub()
            onMoved: root.seek(Math.round(value))
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
            ToolTip.visible: hovered
            ToolTip.text: Accessible.name
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
            ToolTip.visible: hovered
            ToolTip.text: Accessible.name
            onClicked: root.viewportZoom = Math.max(0.5, root.viewportZoom / 1.2)
        }
        AppButton {
            objectName: "previewZoomReset"
            text: Math.round(root.viewportZoom * 100) + "%"
            Accessible.name: qsTr("重置预览视图")
            ToolTip.visible: hovered
            ToolTip.text: Accessible.name
            onClicked: root.resetViewport()
        }
        AppButton {
            text: "+"
            Accessible.name: qsTr("放大预览")
            ToolTip.visible: hovered
            ToolTip.text: Accessible.name
            onClicked: root.viewportZoom = Math.min(4, root.viewportZoom * 1.2)
        }
        AppButton {
            text: "⛶"
            Accessible.name: qsTr("切换全屏")
            ToolTip.visible: hovered
            ToolTip.text: Accessible.name + " (F11)"
            onClicked: root.toggleFullscreen()
        }
    }
}

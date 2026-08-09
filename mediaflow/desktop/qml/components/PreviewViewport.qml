import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Window
import MediaFlow.Native 1.0
import ".."

Rectangle {
    id: root
    objectName: "previewViewport"
    color: Theme.window
    border.color: Theme.transparent
    clip: true

    property string source: ""
    property string runtimeRoot: ""
    property string mltLibrary: ""
    property string mltRepository: ""
    property string mltData: ""
    property bool hdrEnabled: false
    property int profileWidth: 1920
    property int profileHeight: 1080
    property string subtitleText: ""
    property bool exportPreviewActive: false
    property bool transformInteractionEnabled: true
    property var exportPreviewOptions: ({})
    property url watermarkSource: ""
    readonly property var subtitlePreviewStyle: exportPreviewOptions.subtitleStyle || ({})
    readonly property var watermarkPreview: exportPreviewOptions.watermark || ({})
    readonly property int position: preview.position
    readonly property int duration: preview.duration
    readonly property bool playing: preview.playing
    readonly property bool buffering: preview.buffering
    readonly property int bufferedFrames: preview.bufferedFrames
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
    property int visibilityBeforeFullscreen: Window.Windowed

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
        if (host.visibility === Window.FullScreen) {
            host.visibility = visibilityBeforeFullscreen === Window.Maximized
                ? Window.Maximized : Window.Windowed;
        } else {
            visibilityBeforeFullscreen = host.visibility === Window.Maximized
                ? Window.Maximized : Window.Windowed;
            host.visibility = Window.FullScreen;
        }
    }

    function boundedPlaybackFrame(frame) {
        const requestedFrame = Math.max(0, Math.round(frame));
        return preview.duration > 0
            ? Math.min(preview.duration - 1, requestedFrame)
            : requestedFrame;
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

    function confirmPendingPlaybackProgress() {
        if (pendingPlaybackMode === 0 || !preview.playing)
            return;
        const startFrame = boundedPlaybackFrame(pendingPlaybackFrame);
        const boundary = pendingPlaybackMode === 2
            ? Math.max(startFrame, playbackRangeEnd - 1)
            : pendingPlaybackMode < 0 ? 0 : preview.duration - 1;
        const available = Math.abs(boundary - startFrame);
        const required = Math.min(2, Math.max(1, available));
        const progressed = pendingPlaybackMode < 0
            ? startFrame - preview.position
            : preview.position - startFrame;
        if (progressed >= required)
            cancelPendingPlayback();
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
        if (preview.playing) {
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
        anchors.fill: parent
        gradient: Gradient {
            orientation: Gradient.Vertical
            GradientStop { position: 0.0; color: Theme.window }
            GradientStop { position: 1.0; color: Theme.surfaceSunken }
        }
    }

    Item {
        id: previewStage
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.bottom: previewControlBar.top
    }

    Rectangle {
        id: previewSurface
        objectName: "previewSurface"
        anchors.centerIn: previewStage
        readonly property real targetAspectRatio: Math.max(1, root.profileWidth) / Math.max(1, root.profileHeight)
        readonly property real maximumWidth: Math.max(
            1, Math.min(previewStage.width - 72, 1080))
        readonly property real maximumHeight: Math.max(1, previewStage.height - 44)
        width: Math.min(maximumWidth, maximumHeight * targetAspectRatio)
        height: width / targetAspectRatio
        scale: root.viewportZoom
        transform: Translate {
            x: root.viewportPanX
            y: root.viewportPanY
        }
        color: Theme.previewSurface
        border.color: Theme.border
        border.width: 1
        radius: Theme.radiusSmall
        clip: true

        MltPreviewItem {
            id: preview
            objectName: "previewPlayer"
            anchors.fill: parent
            anchors.margins: 1
            source: root.source
            runtimeRoot: root.runtimeRoot
            mltLibrary: root.mltLibrary
            mltRepository: root.mltRepository
            mltData: root.mltData
            hdrEnabled: root.hdrEnabled
            volume: root.previewMuted ? 0.0 : root.previewVolume
            onDroppedFramesChanged: root.droppedFramesReported(droppedFrames)
            onBufferingChanged: {
                if (buffering) {
                    bufferingDelay.restart();
                } else {
                    bufferingDelay.stop();
                    bufferingNotice.visible = false;
                }
            }
            onHdrActiveChanged: root.hdrActiveReported(hdrActive)
            onDurationChanged: if (preview.duration > 0 && root.playbackRequested)
                Qt.callLater(root.attemptPendingPlayback)
            onPositionChanged: {
                root.confirmPendingPlaybackProgress();
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

        Timer {
            id: bufferingDelay
            interval: 300
            repeat: false
            onTriggered: bufferingNotice.visible = preview.buffering
        }

        Rectangle {
            id: bufferingNotice
            objectName: "previewBufferingNotice"
            anchors.centerIn: parent
            visible: false
            width: bufferingText.implicitWidth + 24
            height: 34
            radius: 17
            color: Theme.overlay
            border.color: Theme.borderStrong
            z: 40
            Text {
                id: bufferingText
                anchors.centerIn: parent
                text: preview.bufferedFrames > 0
                    ? qsTr("正在准备画面 · 已缓冲 %1 帧").arg(preview.bufferedFrames)
                    : qsTr("正在准备画面")
                color: Theme.textStrong
                font.pixelSize: Theme.fontSizeCaption
            }
        }

        PreviewTransformOverlay {
            anchors.fill: parent
            previewPosition: preview.position
            interactionVisible: root.transformInteractionEnabled
                && !root.exportPreviewActive
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
            color: Theme.warningSoft
            visible: root.hdrEnabled
            Text {
                id: hdrPreviewLabel
                anchors.centerIn: parent
                text: preview.hdrActive ? qsTr("HDR 预览") : qsTr("HDR 项目 / SDR 预览")
                color: Theme.text
                font.pixelSize: Theme.fontSizeCaption
            }
        }
    }

    Rectangle {
        id: previewControlBar
        objectName: "previewControlBar"
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        height: 52
        color: Theme.surface

        Rectangle {
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: parent.top
            height: 1
            color: Theme.divider
        }
    }

    Flickable {
        id: previewControlsFlick
        objectName: "previewControlsScroll"
        anchors.fill: previewControlBar
        clip: true
        contentWidth: Math.max(width, previewControls.implicitWidth + 32)
        contentHeight: previewControls.implicitHeight
        flickableDirection: Flickable.HorizontalFlick
        boundsBehavior: Flickable.StopAtBounds
        interactive: contentWidth > width
        onWidthChanged: Qt.callLater(function () {
            contentX = Math.max(0, Math.min(
                contentX, Math.max(0, contentWidth - width)));
        })
        onContentWidthChanged: Qt.callLater(function () {
            contentX = Math.max(0, Math.min(
                contentX, Math.max(0, contentWidth - width)));
        })
        ScrollBar.horizontal: AppScrollBar {
            policy: previewControlsFlick.contentWidth > previewControlsFlick.width
                ? ScrollBar.AlwaysOn : ScrollBar.AlwaysOff
        }

        RowLayout {
            id: previewControls
            x: Math.max(16, (previewControlsFlick.width - implicitWidth) / 2)
            height: previewControlsFlick.height
            spacing: 8
        AppIconButton {
            iconName: "previous"
            flat: true
            Accessible.name: qsTr("上一帧")
            toolTipText: Accessible.name
            enabled: preview.duration > 0
            onClicked: preview.seek(Math.max(0, preview.position - 1))
        }
        AppIconButton {
            iconName: preview.playing ? "pause" : "play"
            Accessible.name: preview.playing ? qsTr("暂停") : qsTr("播放")
            toolTipText: Accessible.name + qsTr("（空格）")
            primary: true
            enabled: preview.duration > 0
            onClicked: preview.playing || root.playbackRequested ? root.pause() : root.playPreview()
        }
        AppIconButton {
            iconName: "stop"
            flat: true
            Accessible.name: qsTr("停止并回到开头")
            toolTipText: Accessible.name
            enabled: preview.duration > 0
            onClicked: root.stopPreview()
        }
        Text {
            text: preview.position + " / " + preview.duration
            color: Theme.textMuted
            font.family: Theme.monoFontFamily
            font.pixelSize: Theme.fontSizeCaption
        }
        AppSlider {
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
        AppIconButton {
            iconName: root.previewMuted || root.previewVolume <= 0 ? "mute" : "volume"
            flat: true
            Accessible.name: root.previewMuted ? qsTr("取消静音") : qsTr("静音")
            toolTipText: Accessible.name
            onClicked: root.toggleMute()
        }
        AppSlider {
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
        AppIconButton {
            iconName: "zoom-out"
            flat: true
            Accessible.name: qsTr("缩小预览")
            toolTipText: Accessible.name
            onClicked: root.viewportZoom = Math.max(0.5, root.viewportZoom / 1.2)
        }
        AppButton {
            objectName: "previewZoomReset"
            text: Math.round(root.viewportZoom * 100) + "%"
            compact: true
            quiet: true
            Accessible.name: qsTr("重置预览视图")
            ToolTip.visible: hovered
            ToolTip.text: Accessible.name
            onClicked: root.resetViewport()
        }
        AppIconButton {
            iconName: "zoom-in"
            flat: true
            Accessible.name: qsTr("放大预览")
            toolTipText: Accessible.name
            onClicked: root.viewportZoom = Math.min(4, root.viewportZoom * 1.2)
        }
        AppIconButton {
            iconName: "fullscreen"
            flat: true
            Accessible.name: qsTr("切换全屏")
            toolTipText: Accessible.name + " (F11)"
            onClicked: root.toggleFullscreen()
        }
        }
    }
}

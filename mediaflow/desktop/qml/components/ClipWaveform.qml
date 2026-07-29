import QtQuick
import ".."

Canvas {
    id: root

    objectName: "clipWaveform"
    required property string assetId
    required property int sourceIn
    required property int durationFrames
    required property real speed
    required property bool waveformReady
    required property var viewport
    required property real pixelsPerFrame
    required property real clipContentX
    property bool emphasized: false
    readonly property color strokeColor: emphasized ? Theme.waveform : Theme.waveformMuted

    readonly property real viewportLeft: Math.max(3, viewport.contentX - clipContentX)
    readonly property real viewportRight: Math.min(
        parent ? parent.width - 3 : 0,
        viewport.contentX + viewport.width - clipContentX)
    readonly property int relativeStartFrame: Math.max(
        0, Math.floor(viewportLeft / pixelsPerFrame))
    readonly property int visibleDurationFrames: Math.max(
        0, Math.min(
            durationFrames - relativeStartFrame,
            Math.ceil(width / pixelsPerFrame)))

    x: viewportLeft
    y: 3
    width: Math.max(0, viewportRight - viewportLeft)
    height: parent ? parent.height - 6 : 0
    visible: width > 0 && waveformReady
    opacity: emphasized ? 0.94 : 0.82

    onWidthChanged: requestPaint()
    onXChanged: requestPaint()
    onRelativeStartFrameChanged: requestPaint()
    onVisibleDurationFramesChanged: requestPaint()
    onWaveformReadyChanged: requestPaint()
    onStrokeColorChanged: requestPaint()

    Connections {
        target: mediaController
        function onWaveformDataChanged(changedAssetId) {
            if (changedAssetId === root.assetId)
                root.requestPaint()
        }
    }

    onPaint: {
        var context = getContext("2d")
        context.clearRect(0, 0, width, height)
        var peaks = mediaController.waveformPeaks(
            assetId, sourceIn, durationFrames, speed,
            relativeStartFrame, visibleDurationFrames,
            Math.round(width))
        if (!peaks || peaks.length < 2)
            return
        var count = peaks.length / 2
        context.strokeStyle = strokeColor
        context.lineWidth = 1
        context.beginPath()
        for (var i = 0; i < count; ++i) {
            var px = count === 1 ? 0 : i * (width - 1) / (count - 1)
            var minimum = Number(peaks[i * 2])
            var maximum = Number(peaks[i * 2 + 1])
            context.moveTo(px, height * (0.5 - maximum * 0.46))
            context.lineTo(px, height * (0.5 - minimum * 0.46))
        }
        context.stroke()
    }
}

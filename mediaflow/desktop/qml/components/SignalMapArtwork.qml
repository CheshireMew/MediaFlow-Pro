import QtQuick
import QtQuick.Layouts
import ".."

Item {
    id: root

    property bool compact: false
    property bool showStageLabels: !compact

    Canvas {
        id: canvas
        anchors.fill: parent
        property color signalColor: Theme.accent
        property color cutColor: Theme.cut
        property color frameColor: Theme.borderStrong

        onSignalColorChanged: requestPaint()
        onCutColorChanged: requestPaint()
        onFrameColorChanged: requestPaint()
        onWidthChanged: requestPaint()
        onHeightChanged: requestPaint()

        onPaint: {
            const context = getContext("2d")
            context.reset()

            const inset = root.compact ? 2 : 20
            const top = root.compact ? 2 : 18
            const bottom = root.compact ? height - 2 : height - 34
            const left = inset
            const right = width - inset
            const corner = root.compact ? 15 : 22
            const middle = (top + bottom) / 2
            const amplitude = Math.max(12, (bottom - top) * (root.compact ? 0.22 : 0.18))

            context.strokeStyle = frameColor
            context.lineWidth = 1
            context.beginPath()
            context.moveTo(left + corner, top)
            context.lineTo(left, top)
            context.lineTo(left, top + corner)
            context.moveTo(right - corner, top)
            context.lineTo(right, top)
            context.lineTo(right, top + corner)
            context.moveTo(left, bottom - corner)
            context.lineTo(left, bottom)
            context.lineTo(left + corner, bottom)
            context.moveTo(right, bottom - corner)
            context.lineTo(right, bottom)
            context.lineTo(right - corner, bottom)
            context.stroke()

            const span = right - left
            const points = [
                [left + span * 0.10, middle + amplitude * 0.55],
                [left + span * 0.31, middle - amplitude],
                [left + span * 0.52, middle + amplitude * 0.82],
                [left + span * 0.74, middle - amplitude * 0.74],
                [left + span * 0.90, middle + amplitude * 0.18]
            ]
            context.strokeStyle = signalColor
            context.lineWidth = root.compact ? 2.4 : 3
            context.lineCap = "round"
            context.lineJoin = "round"
            context.beginPath()
            context.moveTo(points[0][0], points[0][1])
            for (let index = 1; index < points.length; ++index)
                context.lineTo(points[index][0], points[index][1])
            context.stroke()

            for (let index = 0; index < points.length; ++index) {
                if (root.compact && index !== 2)
                    continue
                context.fillStyle = index === 2 ? cutColor : signalColor
                context.beginPath()
                context.arc(
                    points[index][0],
                    points[index][1],
                    index === 2 ? (root.compact ? 4 : 5) : 3.5,
                    0,
                    Math.PI * 2)
                context.fill()
            }
        }
    }

    RowLayout {
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        anchors.leftMargin: 12
        anchors.rightMargin: 12
        spacing: 0
        visible: root.showStageLabels

        Repeater {
            model: ["SOURCE", "SCRIPT", "CUT", "FINISH"]
            Text {
                required property string modelData
                Layout.fillWidth: true
                text: modelData
                color: modelData === "CUT" ? Theme.cut : Theme.textMuted
                font.family: Theme.monoFontFamily
                font.pixelSize: Theme.fontSizeCaption
                font.weight: Font.Medium
                font.letterSpacing: 1
                horizontalAlignment: Text.AlignHCenter
            }
        }
    }
}

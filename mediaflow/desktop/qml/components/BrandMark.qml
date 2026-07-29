import QtQuick
import ".."

Canvas {
    id: root

    implicitWidth: 28
    implicitHeight: 28

    onWidthChanged: requestPaint()
    onHeightChanged: requestPaint()
    Component.onCompleted: requestPaint()

    Connections {
        target: Theme
        function onAccentChanged() { root.requestPaint() }
        function onCutChanged() { root.requestPaint() }
        function onBorderStrongChanged() { root.requestPaint() }
    }

    onPaint: {
        const context = getContext("2d");
        const canvasSize = Math.min(width, height);
        const scale = canvasSize / 32;
        context.reset();
        context.save();
        context.translate((width - canvasSize) / 2, (height - canvasSize) / 2);
        context.scale(scale, scale);
        context.lineCap = "round";
        context.lineJoin = "round";

        context.strokeStyle = Theme.borderStrong;
        context.lineWidth = 1.5 / scale;
        const corners = [
            [[11, 4], [4, 4], [4, 11]],
            [[21, 4], [28, 4], [28, 11]],
            [[4, 21], [4, 28], [11, 28]],
            [[21, 28], [28, 28], [28, 21]]
        ];
        for (const points of corners) {
            context.beginPath();
            context.moveTo(points[0][0], points[0][1]);
            context.lineTo(points[1][0], points[1][1]);
            context.lineTo(points[2][0], points[2][1]);
            context.stroke();
        }

        context.strokeStyle = Theme.accent;
        context.lineWidth = 3 / scale;
        context.beginPath();
        context.moveTo(6.5, 21);
        context.lineTo(10.5, 11);
        context.lineTo(16, 21);
        context.lineTo(22, 10.5);
        context.lineTo(25.5, 17);
        context.stroke();

        context.fillStyle = Theme.cut;
        context.beginPath();
        context.arc(16, 21, 2.8, 0, Math.PI * 2);
        context.fill();
        context.restore();
    }
}

import QtQuick
import ".."

Canvas {
    id: root

    width: 20
    height: 20
    property string iconName: "media"
    property color iconColor: Theme.textMuted
    property real strokeWidth: 1.75

    onIconNameChanged: requestPaint()
    onIconColorChanged: requestPaint()
    onStrokeWidthChanged: requestPaint()
    onWidthChanged: requestPaint()
    onHeightChanged: requestPaint()

    onPaint: {
        const context = getContext("2d");
        const canvasSize = Math.min(width, height);
        const scale = canvasSize / 24;
        const offsetX = (width - canvasSize) / 2;
        const offsetY = (height - canvasSize) / 2;

        function begin() {
            context.beginPath();
        }

        function strokeLine(points, closed) {
            begin();
            context.moveTo(points[0][0], points[0][1]);
            for (let index = 1; index < points.length; ++index)
                context.lineTo(points[index][0], points[index][1]);
            if (closed)
                context.closePath();
            context.stroke();
        }

        function fillPolygon(points) {
            begin();
            context.moveTo(points[0][0], points[0][1]);
            for (let index = 1; index < points.length; ++index)
                context.lineTo(points[index][0], points[index][1]);
            context.closePath();
            context.fill();
        }

        function circle(x, y, radius, fill) {
            begin();
            context.arc(x, y, radius, 0, Math.PI * 2);
            fill ? context.fill() : context.stroke();
        }

        function roundedRect(x, y, rectWidth, rectHeight, radius) {
            begin();
            context.moveTo(x + radius, y);
            context.lineTo(x + rectWidth - radius, y);
            context.quadraticCurveTo(x + rectWidth, y, x + rectWidth, y + radius);
            context.lineTo(x + rectWidth, y + rectHeight - radius);
            context.quadraticCurveTo(
                x + rectWidth, y + rectHeight, x + rectWidth - radius, y + rectHeight);
            context.lineTo(x + radius, y + rectHeight);
            context.quadraticCurveTo(x, y + rectHeight, x, y + rectHeight - radius);
            context.lineTo(x, y + radius);
            context.quadraticCurveTo(x, y, x + radius, y);
            context.stroke();
        }

        function drawChevron(direction) {
            if (direction === "left")
                strokeLine([[15, 5], [8, 12], [15, 19]], false);
            else if (direction === "right")
                strokeLine([[9, 5], [16, 12], [9, 19]], false);
            else if (direction === "up")
                strokeLine([[5, 15], [12, 8], [19, 15]], false);
            else
                strokeLine([[5, 9], [12, 16], [19, 9]], false);
        }

        context.reset();
        context.save();
        context.translate(offsetX, offsetY);
        context.scale(scale, scale);
        context.strokeStyle = root.iconColor;
        context.fillStyle = root.iconColor;
        context.lineWidth = root.strokeWidth / scale;
        context.lineCap = "round";
        context.lineJoin = "round";

        const name = root.iconName;
        if (name === "media") {
            roundedRect(3, 4, 18, 16, 2);
            fillPolygon([[10, 8], [16, 12], [10, 16]]);
        } else if (name === "project") {
            begin();
            context.moveTo(3, 7);
            context.lineTo(3, 19);
            context.quadraticCurveTo(3, 21, 5, 21);
            context.lineTo(19, 21);
            context.quadraticCurveTo(21, 21, 21, 19);
            context.lineTo(21, 8);
            context.quadraticCurveTo(21, 6, 19, 6);
            context.lineTo(12, 6);
            context.lineTo(10, 3);
            context.lineTo(5, 3);
            context.quadraticCurveTo(3, 3, 3, 5);
            context.closePath();
            context.stroke();
            strokeLine([[3, 8], [21, 8]], false);
        } else if (name === "versions") {
            roundedRect(4, 6, 14, 14, 2);
            strokeLine([[7, 6], [7, 4], [20, 4], [20, 17], [18, 17]], false);
            circle(11, 13, 3.5, false);
            strokeLine([[11, 10.8], [11, 13], [13, 14.2]], false);
        } else if (name === "transcript") {
            roundedRect(4, 3, 16, 18, 2);
            for (const y of [8, 12, 16])
                strokeLine([[7, y], [17, y]], false);
        } else if (name === "subtitle") {
            roundedRect(3, 4, 18, 16, 2);
            strokeLine([[6, 14], [11, 14]], false);
            strokeLine([[13, 14], [18, 14]], false);
            strokeLine([[8, 17], [16, 17]], false);
        } else if (name === "translate") {
            begin();
            context.moveTo(3, 5);
            context.lineTo(13, 5);
            context.lineTo(13, 14);
            context.lineTo(8, 14);
            context.lineTo(5, 17);
            context.lineTo(5, 14);
            context.lineTo(3, 14);
            context.closePath();
            context.stroke();
            begin();
            context.moveTo(11, 9);
            context.lineTo(21, 9);
            context.lineTo(21, 18);
            context.lineTo(19, 18);
            context.lineTo(19, 21);
            context.lineTo(16, 18);
            context.lineTo(11, 18);
            context.closePath();
            context.stroke();
            strokeLine([[6, 9], [10, 9]], false);
            strokeLine([[14, 13], [18, 13]], false);
        } else if (name === "image") {
            roundedRect(3, 4, 18, 16, 2);
            circle(16.5, 8.5, 2, false);
            strokeLine([[5, 17], [10, 11], [13, 14], [15, 12], [19, 17]], false);
        } else if (name === "web") {
            circle(12, 12, 9, false);
            begin();
            context.ellipse(7.5, 3, 9, 18);
            context.stroke();
            strokeLine([[3, 12], [21, 12]], false);
            begin();
            context.moveTo(5, 7.5);
            context.quadraticCurveTo(12, 10, 19, 7.5);
            context.stroke();
            begin();
            context.moveTo(5, 16.5);
            context.quadraticCurveTo(12, 14, 19, 16.5);
            context.stroke();
        } else if (name === "link") {
            strokeLine([[4, 14], [8, 18], [13, 13], [9, 9], [4, 14]], true);
            strokeLine([[11, 11], [15, 15], [20, 10], [16, 6], [11, 11]], true);
            strokeLine([[9, 15], [15, 9]], false);
        } else if (name === "eye" || name === "eye-off") {
            begin();
            context.moveTo(2.5, 12);
            context.quadraticCurveTo(12, 3.5, 21.5, 12);
            context.quadraticCurveTo(12, 20.5, 2.5, 12);
            context.stroke();
            circle(12, 12, 3, false);
            if (name === "eye-off")
                strokeLine([[4, 4], [20, 20]], false);
        } else if (name === "keyframe") {
            fillPolygon([[12, 3], [21, 12], [12, 21], [3, 12]]);
        } else if (name === "lock" || name === "unlock") {
            roundedRect(5, 10, 14, 11, 2);
            begin();
            if (name === "lock") {
                context.arc(12, 10, 5, Math.PI, 0);
            } else {
                context.arc(12, 10, 5, Math.PI * 1.25, 0);
            }
            context.stroke();
            circle(12, 15, 1.3, true);
            strokeLine([[12, 16], [12, 18]], false);
        } else if (name === "search") {
            circle(10.5, 10.5, 6.5, false);
            strokeLine([[15.5, 15.5], [21, 21]], false);
        } else if (name === "list") {
            for (const y of [6, 12, 18]) {
                circle(5, y, 1, true);
                strokeLine([[9, y], [20, y]], false);
            }
        } else if (name === "thumbnails" || name === "large_thumbnails") {
            if (name === "large_thumbnails") {
                roundedRect(3, 3, 18, 14, 1.5);
                strokeLine([[5, 21], [19, 21]], false);
            } else {
                roundedRect(3, 4, 8, 7, 1.2);
                roundedRect(13, 4, 8, 7, 1.2);
                roundedRect(3, 13, 8, 7, 1.2);
                roundedRect(13, 13, 8, 7, 1.2);
            }
        } else if (name === "highlight") {
            strokeLine(
                [[12, 2.5], [14, 10], [21.5, 12], [14, 14], [12, 21.5],
                 [10, 14], [2.5, 12], [10, 10]],
                true);
        } else if (name === "edit" || name === "cut") {
            circle(6, 6.5, 2.5, false);
            circle(6, 17.5, 2.5, false);
            strokeLine([[8.2, 8], [20, 17.5]], false);
            strokeLine([[8.2, 16], [20, 6.5]], false);
        } else if (name === "audio") {
            strokeLine([[9, 18], [9, 5], [19, 3], [19, 15]], false);
            circle(6, 18, 3, true);
            circle(16, 15, 3, true);
        } else if (name === "microphone") {
            roundedRect(8, 3, 8, 12, 4);
            begin();
            context.arc(12, 11, 7, 0, Math.PI);
            context.stroke();
            strokeLine([[12, 18], [12, 21]], false);
            strokeLine([[8, 21], [16, 21]], false);
        } else if (name === "export") {
            strokeLine([[12, 16], [12, 3]], false);
            strokeLine([[7.5, 7.5], [12, 3], [16.5, 7.5]], false);
            strokeLine([[4, 14], [4, 21], [20, 21], [20, 14]], false);
        } else if (name === "download") {
            strokeLine([[12, 3], [12, 16]], false);
            strokeLine([[7.5, 11.5], [12, 16], [16.5, 11.5]], false);
            strokeLine([[4, 19], [20, 19]], false);
        } else if (name === "tasks") {
            roundedRect(4, 3, 16, 18, 2);
            for (const y of [8, 12, 16]) {
                strokeLine([[7, y], [8.3, y + 1.2], [10.4, y - 1.4]], false);
                strokeLine([[13, y], [17, y]], false);
            }
        } else if (name === "settings") {
            circle(12, 12, 3.4, false);
            for (let angle = 0; angle < Math.PI * 2; angle += Math.PI / 4) {
                strokeLine([
                    [12 + Math.cos(angle) * 6.4, 12 + Math.sin(angle) * 6.4],
                    [12 + Math.cos(angle) * 9, 12 + Math.sin(angle) * 9]
                ], false);
            }
        } else if (name === "undo" || name === "redo") {
            const mirrored = name === "redo";
            const direction = mirrored ? -1 : 1;
            const origin = mirrored ? 17.5 : 6.5;
            strokeLine([
                [origin + direction * 4, 5.5],
                [origin, 9.5],
                [origin + direction * 4, 13.5]
            ], false);
            begin();
            context.arc(12, 13, 7, mirrored ? Math.PI * 1.05 : Math.PI * 1.95,
                        mirrored ? Math.PI * 1.95 : Math.PI * 1.05, mirrored);
            context.stroke();
        } else if (name === "minimize") {
            strokeLine([[6, 17], [18, 17]], false);
        } else if (name === "maximize") {
            context.strokeRect(5.5, 5.5, 13, 13);
        } else if (name === "restore") {
            context.strokeRect(7, 5, 12, 12);
            strokeLine([[17, 17], [17, 19], [5, 19], [5, 7], [7, 7]], false);
        } else if (name === "close" || name === "delete") {
            if (name === "delete") {
                roundedRect(7, 8, 10, 12, 1.5);
                strokeLine([[5, 6], [19, 6]], false);
                strokeLine([[9, 3.5], [15, 3.5]], false);
                strokeLine([[10, 11], [10, 17]], false);
                strokeLine([[14, 11], [14, 17]], false);
            } else {
                strokeLine([[6, 6], [18, 18]], false);
                strokeLine([[18, 6], [6, 18]], false);
            }
        } else if (name === "play") {
            fillPolygon([[8, 5], [19, 12], [8, 19]]);
        } else if (name === "pause") {
            context.fillRect(7, 5, 3.5, 14);
            context.fillRect(13.5, 5, 3.5, 14);
        } else if (name === "stop") {
            roundedRect(6, 6, 12, 12, 1.5);
        } else if (name === "previous") {
            context.fillRect(5, 5, 2.2, 14);
            fillPolygon([[18, 5], [8, 12], [18, 19]]);
        } else if (name === "volume" || name === "mute") {
            fillPolygon([[4, 10], [8, 10], [13, 6], [13, 18], [8, 14], [4, 14]]);
            if (name === "mute") {
                strokeLine([[16, 9], [21, 15]], false);
                strokeLine([[21, 9], [16, 15]], false);
            } else {
                begin();
                context.arc(13, 12, 4.5, -Math.PI / 3, Math.PI / 3);
                context.stroke();
                begin();
                context.arc(13, 12, 8, -Math.PI / 3, Math.PI / 3);
                context.stroke();
            }
        } else if (name === "zoom" || name === "zoom-in" || name === "zoom-out") {
            circle(10.5, 10.5, 6, false);
            strokeLine([[15, 15], [20, 20]], false);
            strokeLine([[7.5, 10.5], [13.5, 10.5]], false);
            if (name !== "zoom-out")
                strokeLine([[10.5, 7.5], [10.5, 13.5]], false);
        } else if (name === "fullscreen") {
            strokeLine([[9, 4], [4, 4], [4, 9]], false);
            strokeLine([[15, 4], [20, 4], [20, 9]], false);
            strokeLine([[4, 15], [4, 20], [9, 20]], false);
            strokeLine([[20, 15], [20, 20], [15, 20]], false);
        } else if (name === "add") {
            strokeLine([[12, 4], [12, 20]], false);
            strokeLine([[4, 12], [20, 12]], false);
        } else if (name === "minus") {
            strokeLine([[4, 12], [20, 12]], false);
        } else if (name === "duplicate") {
            roundedRect(4, 7, 12, 13, 1.8);
            roundedRect(8, 4, 12, 13, 1.8);
        } else if (name === "transition") {
            strokeLine([[4, 6], [20, 18]], false);
            strokeLine([[4, 18], [20, 6]], false);
        } else if (name === "transition-zoom") {
            circle(12, 12, 7, false);
            circle(12, 12, 3, false);
        } else if (name === "transition-black") {
            roundedRect(5, 5, 14, 14, 1.5);
            context.fillRect(8, 8, 8, 8);
        } else if (name === "up") {
            drawChevron("up");
        } else if (name === "down") {
            drawChevron("down");
        } else if (name === "chevron-left") {
            drawChevron("left");
        } else if (name === "chevron-right") {
            drawChevron("right");
        } else if (name === "chevron-up") {
            drawChevron("up");
        } else if (name === "chevron-down") {
            drawChevron("down");
        } else if (name === "more") {
            circle(5, 12, 1.5, true);
            circle(12, 12, 1.5, true);
            circle(19, 12, 1.5, true);
        } else if (name === "drag") {
            for (const x of [9, 15]) {
                for (const y of [6, 12, 18])
                    circle(x, y, 1.35, true);
            }
        } else if (name === "check") {
            strokeLine([[4.5, 12.5], [9.5, 17.5], [19.5, 6.5]], false);
        } else if (name === "warning") {
            strokeLine([[12, 3], [22, 21], [2, 21]], true);
            strokeLine([[12, 8], [12, 15]], false);
            circle(12, 18, 1.1, true);
        } else if (name.length > 0) {
            console.warn("Unknown AppIcon name:", name);
            roundedRect(4, 4, 16, 16, 2);
            strokeLine([[7, 7], [17, 17]], false);
            strokeLine([[17, 7], [7, 17]], false);
        }

        context.restore();
    }
}

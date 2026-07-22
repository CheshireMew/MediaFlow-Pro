import QtQuick

Canvas {
    id: root
    width: 20
    height: 20
    property string iconName: "media"
    property color iconColor: "#9aa4b5"

    onIconNameChanged: requestPaint()
    onIconColorChanged: requestPaint()
    onWidthChanged: requestPaint()
    onHeightChanged: requestPaint()

    onPaint: {
        const ctx = getContext("2d");
        ctx.reset();
        ctx.strokeStyle = root.iconColor;
        ctx.fillStyle = root.iconColor;
        ctx.lineWidth = 1.7;
        ctx.lineCap = "round";
        ctx.lineJoin = "round";

        if (root.iconName === "media") {
            ctx.strokeRect(2, 3, 16, 14);
            ctx.beginPath();
            ctx.moveTo(8, 7);
            ctx.lineTo(13.5, 10);
            ctx.lineTo(8, 13);
            ctx.closePath();
            ctx.fill();
        } else if (root.iconName === "transcript") {
            ctx.strokeRect(2.5, 3, 15, 14);
            for (let y of [7, 10, 13]) {
                ctx.beginPath();
                ctx.moveTo(5.5, y);
                ctx.lineTo(14.5, y);
                ctx.stroke();
            }
        } else if (root.iconName === "subtitle") {
            ctx.strokeRect(1.5, 3.5, 17, 13);
            ctx.beginPath();
            ctx.arc(7, 10, 2.4, Math.PI * 0.35, Math.PI * 1.65);
            ctx.arc(13.3, 10, 2.4, Math.PI * 0.35, Math.PI * 1.65);
            ctx.stroke();
        } else if (root.iconName === "translate") {
            ctx.strokeRect(1.5, 3, 11, 9);
            ctx.strokeRect(7.5, 8, 11, 9);
            ctx.beginPath();
            ctx.moveTo(5, 15);
            ctx.lineTo(2.5, 17.5);
            ctx.lineTo(3.5, 12);
            ctx.moveTo(15, 5);
            ctx.lineTo(17.5, 2.5);
            ctx.lineTo(16.5, 8);
            ctx.stroke();
        } else if (root.iconName === "highlight") {
            ctx.beginPath();
            ctx.moveTo(10, 1.5);
            ctx.lineTo(11.8, 8.2);
            ctx.lineTo(18.5, 10);
            ctx.lineTo(11.8, 11.8);
            ctx.lineTo(10, 18.5);
            ctx.lineTo(8.2, 11.8);
            ctx.lineTo(1.5, 10);
            ctx.lineTo(8.2, 8.2);
            ctx.closePath();
            ctx.stroke();
        } else if (root.iconName === "edit") {
            ctx.beginPath();
            ctx.arc(4.5, 5, 2.3, 0, Math.PI * 2);
            ctx.moveTo(6.5, 6.3);
            ctx.lineTo(17, 14.5);
            ctx.moveTo(4.5, 15);
            ctx.arc(4.5, 15, 2.3, 0, Math.PI * 2);
            ctx.moveTo(6.5, 13.7);
            ctx.lineTo(17, 5.5);
            ctx.stroke();
        } else if (root.iconName === "audio") {
            ctx.beginPath();
            ctx.moveTo(8, 15);
            ctx.lineTo(8, 4);
            ctx.lineTo(16, 2.5);
            ctx.lineTo(16, 12.5);
            ctx.stroke();
            ctx.beginPath();
            ctx.arc(5.5, 15.5, 2.5, 0, Math.PI * 2);
            ctx.arc(13.5, 13, 2.5, 0, Math.PI * 2);
            ctx.fill();
        } else if (root.iconName === "export") {
            ctx.beginPath();
            ctx.moveTo(10, 14);
            ctx.lineTo(10, 3);
            ctx.moveTo(6, 7);
            ctx.lineTo(10, 3);
            ctx.lineTo(14, 7);
            ctx.moveTo(3, 12);
            ctx.lineTo(3, 17);
            ctx.lineTo(17, 17);
            ctx.lineTo(17, 12);
            ctx.stroke();
        } else if (root.iconName === "tasks") {
            ctx.strokeRect(3, 2.5, 14, 15);
            for (let y of [7, 11, 15]) {
                ctx.beginPath();
                ctx.moveTo(6, y);
                ctx.lineTo(7.5, y + 1.3);
                ctx.lineTo(10, y - 1.5);
                ctx.moveTo(11.5, y);
                ctx.lineTo(15, y);
                ctx.stroke();
            }
        } else {
            ctx.beginPath();
            ctx.arc(10, 10, 4, 0, Math.PI * 2);
            ctx.stroke();
            for (let angle = 0; angle < Math.PI * 2; angle += Math.PI / 4) {
                ctx.beginPath();
                ctx.moveTo(10 + Math.cos(angle) * 6, 10 + Math.sin(angle) * 6);
                ctx.lineTo(10 + Math.cos(angle) * 9, 10 + Math.sin(angle) * 9);
                ctx.stroke();
            }
        }
    }
}

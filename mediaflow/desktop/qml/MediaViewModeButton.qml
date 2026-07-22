import QtQuick
import QtQuick.Controls
import "."

AbstractButton {
    id: root

    required property string iconKind
    required property string toolTipText
    readonly property color iconColor: checked || hovered ? Theme.text : Theme.textMuted

    implicitWidth: 32
    implicitHeight: 32
    checkable: false
    padding: 0
    Accessible.name: toolTipText

    onIconColorChanged: icon.requestPaint()
    onIconKindChanged: icon.requestPaint()

    background: Rectangle {
        radius: 5
        color: root.checked ? Theme.accentSoft
            : root.hovered ? Theme.surfaceHover : Theme.surfaceRaised
        border.color: root.checked ? Theme.accent : Theme.border
    }

    contentItem: Canvas {
        id: icon

        onPaint: {
            const context = getContext("2d");
            context.clearRect(0, 0, width, height);
            context.strokeStyle = root.iconColor;
            context.fillStyle = root.iconColor;
            context.lineWidth = 1.5;
            context.lineCap = "round";
            context.lineJoin = "round";
            const left = Math.round((width - 18) / 2);
            const top = Math.round((height - 18) / 2);

            if (root.iconKind === "list") {
                for (let row = 0; row < 3; row++) {
                    const y = top + 2 + row * 6;
                    context.fillRect(left, y, 3, 3);
                    context.beginPath();
                    context.moveTo(left + 6, y + 1.5);
                    context.lineTo(left + 18, y + 1.5);
                    context.stroke();
                }
                return;
            }

            if (root.iconKind === "thumbnails") {
                for (let row = 0; row < 2; row++) {
                    for (let column = 0; column < 2; column++) {
                        context.strokeRect(
                            left + column * 10 + 0.75,
                            top + row * 10 + 0.75,
                            7.5,
                            7.5);
                    }
                }
                return;
            }

            context.strokeRect(left + 0.75, top + 0.75, 16.5, 11.5);
            context.beginPath();
            context.moveTo(left + 2, top + 15.5);
            context.lineTo(left + 16, top + 15.5);
            context.stroke();
        }
    }

    ToolTip.visible: hovered
    ToolTip.text: toolTipText
    ToolTip.delay: 350
}

import QtQuick
import QtQuick.Controls
import ".."

Rectangle {
    id: root
    property string text: ""
    property string tone: "neutral"
    readonly property color foreground: tone === "success"
        ? Theme.success : tone === "warning" ? Theme.warning : Theme.textMuted
    readonly property color background: tone === "success"
        ? Theme.successSoft : tone === "warning" ? Theme.warningSoft : Theme.surfaceRaised
    implicitWidth: label.implicitWidth + 14
    implicitHeight: 24
    radius: 12
    color: background
    border.color: foreground

    Text {
        id: label
        anchors.centerIn: parent
        text: root.text
        color: root.foreground
        font.pixelSize: Theme.fontSizeCaption
        font.weight: Font.DemiBold
    }
}

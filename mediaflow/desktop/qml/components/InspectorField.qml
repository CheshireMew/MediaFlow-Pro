import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import ".."

RowLayout {
    id: root
    property string label: ""
    property alias text: editor.text
    property string placeholderText: ""
    signal valueAccepted()
    spacing: 8

    Text {
        Layout.preferredWidth: 68
        text: root.label
        color: Theme.textMuted
        font.pixelSize: 10
    }
    TextField {
        id: editor
        Layout.fillWidth: true
        implicitHeight: 30
        color: Theme.text
        placeholderText: root.placeholderText
        placeholderTextColor: Theme.textMuted
        selectByMouse: true
        font.pixelSize: 10
        validator: DoubleValidator { notation: DoubleValidator.StandardNotation }
        background: Rectangle {
            radius: Theme.radiusSmall
            color: Theme.surfaceRaised
            border.color: editor.activeFocus ? Theme.accent : Theme.border
        }
        onEditingFinished: root.valueAccepted()
    }
}

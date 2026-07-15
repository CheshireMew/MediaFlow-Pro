import QtQuick
import QtQuick.Layouts
import ".."

ColumnLayout {
    property string iconText: "＋"
    property string title: "暂无内容"
    property string description: ""
    spacing: 10

    Item { Layout.fillHeight: true }
    Rectangle {
        Layout.alignment: Qt.AlignHCenter
        width: 48
        height: 48
        radius: 14
        color: Theme.surfaceRaised
        border.color: Theme.border
        Text {
            anchors.centerIn: parent
            text: iconText
            color: Theme.textMuted
            font.pixelSize: 24
        }
    }
    Text {
        Layout.alignment: Qt.AlignHCenter
        text: title
        color: Theme.text
        font.pixelSize: 15
        font.weight: Font.DemiBold
    }
    Text {
        Layout.alignment: Qt.AlignHCenter
        Layout.maximumWidth: 240
        text: description
        color: Theme.textMuted
        font.pixelSize: 12
        wrapMode: Text.WordWrap
        horizontalAlignment: Text.AlignHCenter
    }
    Item { Layout.fillHeight: true }
}

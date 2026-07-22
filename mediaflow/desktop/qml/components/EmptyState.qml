import QtQuick
import QtQuick.Layouts
import ".."

Item {
    property string iconText: "＋"
    property string title: "暂无内容"
    property string description: ""
    property bool iconVisible: true
    property int contentMaximumWidth: 240
    property int titleFontSize: Theme.fontSizeTitleSmall
    property int descriptionFontSize: Theme.fontSizeBodySmall

    implicitWidth: content.implicitWidth
    implicitHeight: content.implicitHeight

    ColumnLayout {
        id: content
        anchors.centerIn: parent
        width: Math.min(contentMaximumWidth, Math.max(0, parent.width - 32))
        spacing: 10

        Rectangle {
            visible: iconVisible
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
            Layout.fillWidth: true
            text: title
            color: Theme.text
            font.pixelSize: titleFontSize
            font.weight: Font.DemiBold
            horizontalAlignment: Text.AlignHCenter
        }
        Text {
            Layout.fillWidth: true
            text: description
            color: Theme.textMuted
            font.pixelSize: descriptionFontSize
            wrapMode: Text.WordWrap
            horizontalAlignment: Text.AlignHCenter
        }
    }
}

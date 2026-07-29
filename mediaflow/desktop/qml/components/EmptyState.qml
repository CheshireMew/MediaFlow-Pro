import QtQuick
import QtQuick.Layouts
import ".."

Item {
    id: root
    property string iconName: "project"
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

        Item {
            visible: iconVisible
            Layout.alignment: Qt.AlignHCenter
            implicitWidth: 56
            implicitHeight: 52

            Rectangle {
                anchors.fill: parent
                radius: Theme.radius
                color: Theme.surfaceRaised
                border.color: Theme.border
            }

            Rectangle {
                anchors.left: parent.left
                anchors.top: parent.top
                anchors.leftMargin: 7
                anchors.topMargin: 7
                width: 13
                height: 2
                radius: 1
                color: Theme.accent
            }

            AppIcon {
                anchors.centerIn: parent
                width: 24
                height: 24
                iconName: root.iconName
                iconColor: Theme.textSubtle
                strokeWidth: 1.8
            }

            Rectangle {
                anchors.right: parent.right
                anchors.bottom: parent.bottom
                anchors.rightMargin: 7
                anchors.bottomMargin: 7
                width: 5
                height: 5
                radius: 3
                color: Theme.cut
            }
        }
        Text {
            objectName: "emptyStateTitle"
            Layout.fillWidth: true
            text: title
            color: Theme.text
            font.pixelSize: titleFontSize
            font.weight: Font.DemiBold
            horizontalAlignment: Text.AlignHCenter
        }
        Text {
            objectName: "emptyStateDescription"
            Layout.fillWidth: true
            text: description
            color: Theme.textMuted
            font.pixelSize: descriptionFontSize
            wrapMode: Text.WordWrap
            horizontalAlignment: Text.AlignHCenter
        }
    }
}

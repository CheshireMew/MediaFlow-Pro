import QtQuick

AppButton {
    id: control

    property string indicatorIconName: "chevron-down"
    property int indicatorIconSize: 14
    property int contentSpacing: 6

    contentItem: Item {
        implicitWidth: contentRow.implicitWidth
        implicitHeight: Math.max(label.implicitHeight, indicator.implicitHeight)

        Item {
            id: contentRow
            anchors.centerIn: parent
            width: Math.min(parent.width, implicitWidth)
            height: implicitHeight
            implicitWidth: label.implicitWidth + control.contentSpacing + indicator.width
            implicitHeight: Math.max(label.implicitHeight, indicator.height)

            Text {
                id: label
                anchors.left: parent.left
                anchors.right: indicator.left
                anchors.rightMargin: control.contentSpacing
                anchors.verticalCenter: parent.verticalCenter
                text: control.text
                color: control.foregroundColor
                font: control.font
                elide: Text.ElideRight
            }

            AppIcon {
                id: indicator
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                width: control.indicatorIconSize
                height: control.indicatorIconSize
                iconName: control.indicatorIconName
                iconColor: control.foregroundColor
            }
        }

        readonly property bool truncated: label.truncated
    }
}

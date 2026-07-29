import QtQuick

AppButton {
    id: control

    property string indicatorIconName: "chevron-down"
    property int indicatorIconSize: 14
    property int contentSpacing: 6

    contentItem: Item {
        implicitWidth: contentRow.implicitWidth
        implicitHeight: Math.max(label.implicitHeight, indicator.implicitHeight)

        Row {
            id: contentRow
            anchors.centerIn: parent
            spacing: control.contentSpacing

            Text {
                id: label
                anchors.verticalCenter: parent.verticalCenter
                text: control.text
                color: control.foregroundColor
                font: control.font
                elide: Text.ElideRight
            }

            AppIcon {
                id: indicator
                anchors.verticalCenter: parent.verticalCenter
                width: control.indicatorIconSize
                height: control.indicatorIconSize
                iconName: control.indicatorIconName
                iconColor: control.foregroundColor
            }
        }
    }
}

pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import ".."

ComboBox {
    id: control
    implicitHeight: Theme.controlHeight
    leftPadding: 12
    rightPadding: 34
    hoverEnabled: true
    focusPolicy: Qt.StrongFocus
    font.pixelSize: Theme.fontSizeBody
    palette.button: Theme.control
    palette.buttonText: Theme.text
    palette.window: Theme.popup
    palette.text: Theme.text
    palette.highlight: Theme.selectionSoft
    palette.highlightedText: Theme.text
    contentItem: Text {
        text: control.displayText
        color: control.enabled ? Theme.text : Theme.textDisabled
        font: control.font
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
    }
    indicator: AppIcon {
        implicitWidth: 14
        implicitHeight: 14
        x: control.width - width - 12
        y: Math.round((control.height - height) / 2)
        iconName: "chevron-down"
        iconColor: control.enabled ? Theme.textSubtle : Theme.textDisabled
    }
    background: Rectangle {
        radius: Theme.radiusSmall
        color: !control.enabled
            ? Theme.controlDisabled
            : control.down
            ? Theme.controlPressed
            : control.hovered ? Theme.controlHover : Theme.control
        border.color: control.activeFocus
            ? Theme.focusColor
            : control.hovered ? Theme.borderStrong : Theme.borderSubtle
        border.width: control.activeFocus ? 2 : 1

        Behavior on color {
            ColorAnimation { duration: Theme.durationFast }
        }
        Behavior on border.color {
            ColorAnimation { duration: Theme.durationFast }
        }
    }
    delegate: ItemDelegate {
        id: delegateItem
        required property int index
        width: control.width
        implicitHeight: Theme.controlHeight
        highlighted: control.highlightedIndex === index
        hoverEnabled: true
        contentItem: Text {
            text: control.textAt(delegateItem.index)
            color: parent.enabled ? Theme.text : Theme.textDisabled
            font: control.font
            verticalAlignment: Text.AlignVCenter
            elide: Text.ElideRight
        }
        background: Rectangle {
            color: delegateItem.highlighted || delegateItem.hovered
                ? Theme.selectionSoft : Theme.popup
            radius: Theme.radiusSmall
        }
    }
    popup: Popup {
        id: popup
        objectName: "appComboBoxPopup"
        y: control.height + 2
        width: control.width
        implicitHeight: Math.min(contentItem.implicitHeight + topPadding + bottomPadding, 320)
        topPadding: 2
        bottomPadding: 2
        leftPadding: 1
        rightPadding: 1
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutsideParent
        contentItem: ListView {
            clip: true
            implicitHeight: contentHeight
            model: popup.visible ? control.delegateModel : null
            currentIndex: control.highlightedIndex
            ScrollIndicator.vertical: AppScrollIndicator {}
        }
        background: Rectangle {
            radius: Theme.radiusSmall
            color: Theme.popup
            border.color: Theme.borderStrong
            border.width: 1
        }
    }
}

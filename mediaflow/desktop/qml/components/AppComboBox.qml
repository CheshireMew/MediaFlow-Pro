import QtQuick
import QtQuick.Controls
import ".."

ComboBox {
    id: control
    implicitHeight: 36
    leftPadding: 12
    rightPadding: 34
    font.pixelSize: Theme.fontSizeBody
    palette.button: Theme.surfaceRaised
    palette.buttonText: Theme.text
    palette.window: Theme.surfaceFloating
    palette.text: Theme.text
    palette.highlight: Theme.accentSoft
    palette.highlightedText: Theme.text
    contentItem: Text {
        text: control.displayText
        color: control.enabled ? Theme.text : Theme.textMuted
        font: control.font
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
    }
    indicator: Text {
        x: control.width - width - 12
        y: Math.round((control.height - height) / 2)
        text: "▾"
        color: control.enabled ? Theme.textMuted : Theme.borderStrong
        font.pixelSize: Theme.fontSizeBodySmall
    }
    background: Rectangle {
        radius: Theme.radiusSmall
        color: control.down ? Theme.surfaceHover : Theme.surfaceRaised
        border.color: control.activeFocus ? Theme.accent
            : control.hovered ? Theme.borderStrong : Theme.border
        border.width: control.activeFocus ? 2 : 1
    }
    delegate: ItemDelegate {
        width: control.width
        implicitHeight: 38
        highlighted: control.highlightedIndex === index
        hoverEnabled: true
        contentItem: Text {
            text: control.textAt(index)
            color: Theme.text
            font: control.font
            verticalAlignment: Text.AlignVCenter
            elide: Text.ElideRight
        }
        background: Rectangle {
            color: parent.highlighted || parent.hovered
                ? Theme.accentSoft : Theme.surfaceFloating
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
            ScrollIndicator.vertical: ScrollIndicator {}
        }
        background: Rectangle {
            radius: Theme.radiusSmall
            color: Theme.surfaceFloating
            border.color: Theme.borderStrong
            border.width: 1
        }
    }
}

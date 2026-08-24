import QtQuick
import QtQuick.Layouts
import ".."

ColumnLayout {
    id: root
    required property var field
    property var options: []
    property bool showLabel: true
    signal valueCommitted(var value)
    Layout.fillWidth: true
    spacing: 4

    readonly property var definition: field.descriptor || ({})
    readonly property var constraints: definition.constraints || ({})
    readonly property var choiceItems: options.length > 0
        ? options : (constraints.choices || [])

    function choiceIndex() {
        for (let index = 0; index < choiceItems.length; index++) {
            if (choiceItems[index].value === field.value)
                return index;
        }
        return -1;
    }

    RowLayout {
        Layout.fillWidth: true
        visible: root.showLabel
        Text {
            Layout.fillWidth: true
            text: String(root.definition.label || root.definition.id || "")
            color: Theme.text
            font.pixelSize: Theme.fontSizeCaption
            elide: Text.ElideRight
        }
        Text {
            visible: String(root.definition.unit || "").length > 0
            text: String(root.definition.unit || "")
            color: Theme.textMuted
            font.pixelSize: Theme.fontSizeCaption
        }
    }

    Loader {
        Layout.fillWidth: true
        sourceComponent: {
            if (root.definition.control === "toggle")
                return toggleEditor;
            if (root.definition.control === "select")
                return selectEditor;
            if (root.definition.control === "slider")
                return sliderEditor;
            if (root.definition.control === "number")
                return numberEditor;
            return textEditor;
        }
    }

    Component {
        id: textEditor
        AppTextField {
            Layout.fillWidth: true
            text: String(root.field.value ?? "")
            placeholderText: String(root.definition.kind || "")
            onEditingFinished: root.valueCommitted(text)
        }
    }

    Component {
        id: numberEditor
        AppTextField {
            Layout.fillWidth: true
            text: String(root.field.value ?? root.definition.default ?? 0)
            onEditingFinished: root.valueCommitted(
                root.definition.kind === "integer"
                    ? Math.round(Number(text)) : Number(text))
        }
    }

    Component {
        id: toggleEditor
        AppCheckBox {
            text: checked ? qsTr("已启用") : qsTr("未启用")
            checked: Boolean(root.field.value)
            onClicked: root.valueCommitted(checked)
        }
    }

    Component {
        id: selectEditor
        AppComboBox {
            Layout.fillWidth: true
            model: root.choiceItems
            textRole: "label"
            valueRole: "value"
            currentIndex: root.choiceIndex()
            onActivated: root.valueCommitted(currentValue)
        }
    }

    Component {
        id: sliderEditor
        RowLayout {
            Layout.fillWidth: true
            AppSlider {
                id: slider
                Layout.fillWidth: true
                from: Number(root.constraints.minimum ?? 0)
                to: Number(root.constraints.maximum ?? 1)
                stepSize: Number(root.constraints.step ?? 0.01)
                value: Number(root.field.value ?? root.definition.default ?? from)
                onPressedChanged: if (!pressed)
                    root.valueCommitted(root.definition.kind === "integer"
                        ? Math.round(value) : value)
            }
            AppTextField {
                Layout.preferredWidth: 78
                text: root.definition.kind === "integer"
                    ? String(Math.round(slider.value))
                    : Number(slider.value).toFixed(
                        Number(root.constraints.step ?? 0.01) < 1 ? 2 : 0)
                onEditingFinished: root.valueCommitted(
                    root.definition.kind === "integer"
                        ? Math.round(Number(text)) : Number(text))
            }
        }
    }
}

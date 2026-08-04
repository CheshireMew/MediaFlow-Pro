import QtQuick
import QtQuick.Layouts
import "."
import "components"

ColumnLayout {
    id: root
    required property var descriptor
    property int playheadFrame: 0
    Layout.fillWidth: true
    spacing: 5

    function commit(value) {
        webController.updateDescriptorValue(
            String(descriptor.target),
            String(descriptor.source_id),
            value)
    }

    function collaborationPath() {
        const target = String(descriptor.target || "");
        let source = String(descriptor.source_id || "")
            .replace(/~/g, "~0").replace(/\//g, "~1");
        if (target === "layer")
            source = source.replace(/\./g, "/");
        const section = target === "parameter" ? "parameters" :
            target === "layer" ? "layers" : target;
        return "/web/clips/" + webController.webClipId
            + "/" + section + "/" + source;
    }

    RowLayout {
        Layout.fillWidth: true
        Text {
            Layout.fillWidth: true
            text: String(root.descriptor.label || root.descriptor.source_id)
            color: Theme.text
            font.pixelSize: Theme.fontSizeCaption
            elide: Text.ElideRight
        }
        Text {
            visible: String(root.descriptor.unit || "").length > 0
            text: String(root.descriptor.unit || "")
            color: Theme.textMuted
            font.pixelSize: 10
        }
        AppIconButton {
            visible: root.descriptor.target === "layer"
                || root.descriptor.target === "parameter"
            implicitWidth: 28
            implicitHeight: 26
            iconSize: 14
            iconName: root.descriptor.locked ? "lock" : "unlock"
            flat: false
            toolTipText: root.descriptor.locked
                ? qsTr("允许自动化修改") : qsTr("保留人工调整")
            onClicked: webController.setDescriptorLocked(
                String(root.descriptor.target),
                String(root.descriptor.source_id),
                !Boolean(root.descriptor.locked))
        }
    }

    Loader {
        Layout.fillWidth: true
        sourceComponent: {
            if (root.descriptor.control === "toggle")
                return toggleEditor;
            if (root.descriptor.control === "select")
                return selectEditor;
            if (root.descriptor.control === "slider")
                return sliderEditor;
            return textEditor;
        }
    }

    RowLayout {
        Layout.fillWidth: true
        visible: root.descriptor.timeline === "keyframe"
        AppButton {
            Layout.fillWidth: true
            text: qsTr("在播放头添加关键帧")
            onClicked: webTimelineController.setDescriptorKeyframeAtFrame(
                String(root.descriptor.target),
                String(root.descriptor.source_id),
                root.descriptor.value,
                "ease_in_out",
                root.playheadFrame)
        }
        AppIconButton {
            implicitWidth: 32
            implicitHeight: 30
            iconName: "trash"
            flat: false
            toolTipText: qsTr("移除播放头处关键帧")
            onClicked: webTimelineController.removeDescriptorKeyframeAtFrame(
                String(root.descriptor.target),
                String(root.descriptor.source_id),
                root.playheadFrame)
        }
    }

    Component {
        id: textEditor
        AppTextField {
            Layout.fillWidth: true
            collaborationPath: root.collaborationPath()
            text: root.descriptor.target === "data"
                ? String(root.descriptor.valueText || "")
                : String(root.descriptor.value ?? "")
            placeholderText: String(root.descriptor.kind || "")
            onEditingFinished: root.commit(text)
        }
    }

    Component {
        id: toggleEditor
        AppCheckBox {
            text: checked ? qsTr("已启用") : qsTr("未启用")
            checked: Boolean(root.descriptor.value)
            onClicked: root.commit(checked)
        }
    }

    Component {
        id: selectEditor
        AppComboBox {
            Layout.fillWidth: true
            model: root.descriptor.constraints.choices || []
            currentIndex: Math.max(0, model.indexOf(root.descriptor.value))
            onActivated: root.commit(currentValue)
        }
    }

    Component {
        id: sliderEditor
        RowLayout {
            Layout.fillWidth: true
            AppSlider {
                id: slider
                Layout.fillWidth: true
                from: Number(root.descriptor.constraints.minimum ?? 0)
                to: Number(root.descriptor.constraints.maximum ?? 1)
                stepSize: Number(root.descriptor.constraints.step ?? 0.01)
                value: Number(root.descriptor.value ?? from)
                onPressedChanged: if (!pressed)
                    root.commit(root.descriptor.kind === "integer"
                        ? Math.round(value) : value)
            }
            AppTextField {
                Layout.preferredWidth: 78
                collaborationPath: root.collaborationPath()
                text: root.descriptor.kind === "integer"
                    ? String(Math.round(slider.value))
                    : Number(slider.value).toFixed(2)
                onEditingFinished: root.commit(root.descriptor.kind === "integer"
                    ? Math.round(Number(text)) : Number(text))
            }
        }
    }
}

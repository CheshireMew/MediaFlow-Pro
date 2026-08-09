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

    readonly property var definition: descriptor.descriptor || ({})

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
            text: String(root.definition.label || root.descriptor.source_id)
            color: Theme.text
            font.pixelSize: Theme.fontSizeCaption
            elide: Text.ElideRight
        }
        Text {
            visible: String(root.definition.unit || "").length > 0
            text: String(root.definition.unit || "")
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
        AppButton {
            implicitHeight: 26
            text: qsTr("复制 CLI")
            onClicked: automationController.copyWebFieldUpdateRequest(
                String(root.descriptor.target),
                String(root.descriptor.source_id),
                root.descriptor.value)
        }
    }

    EditorFieldControl {
        field: root.descriptor
        showLabel: false
        onValueCommitted: value => root.commit(value)
    }

    RowLayout {
        Layout.fillWidth: true
        visible: root.definition.timeline === "keyframe"
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
            iconName: "delete"
            flat: false
            toolTipText: qsTr("移除播放头处关键帧")
            onClicked: webTimelineController.removeDescriptorKeyframeAtFrame(
                String(root.descriptor.target),
                String(root.descriptor.source_id),
                root.playheadFrame)
        }
        AppButton {
            text: qsTr("复制设置请求")
            onClicked: automationController.copyWebKeyframeSetRequest(
                String(root.descriptor.target),
                String(root.descriptor.source_id),
                root.descriptor.value,
                "ease_in_out",
                root.playheadFrame)
        }
        AppButton {
            text: qsTr("复制移除请求")
            onClicked: automationController.copyWebKeyframeRemoveRequest(
                String(root.descriptor.target),
                String(root.descriptor.source_id),
                root.playheadFrame)
        }
    }
}

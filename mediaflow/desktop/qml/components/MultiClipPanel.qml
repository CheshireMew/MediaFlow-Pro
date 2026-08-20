import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import ".."

AppScrollView {
    id: root
    objectName: "multiClipInspector"
    clip: true
    contentWidth: availableWidth
    readonly property var summary: mediaflow.timelineViewController.selectedClipsSummary

    function shown(value, fallback) {
        return value === null || value === undefined ? fallback : String(value);
    }

    ColumnLayout {
        width: root.availableWidth
        spacing: 12

        Item { Layout.preferredHeight: 4 }
        Text {
            Layout.fillWidth: true
            Layout.leftMargin: 18
            Layout.rightMargin: 18
            text: qsTr("已选择 %1 个片段，共 %2 帧")
                .arg(root.summary.count || 0)
                .arg(root.summary.totalDurationFrames || 0)
            color: Theme.text
            font.pixelSize: Theme.fontSizeBody
            font.weight: Font.DemiBold
        }
        Text {
            Layout.fillWidth: true
            Layout.leftMargin: 18
            Layout.rightMargin: 18
            text: qsTr("下面的值会一次应用到全部所选片段，并作为一次操作撤销。混合值显示为“多值”。")
            color: Theme.textMuted
            font.pixelSize: Theme.fontSizeCaption
            wrapMode: Text.WordWrap
        }
        GridLayout {
            Layout.fillWidth: true
            Layout.leftMargin: 18
            Layout.rightMargin: 18
            columns: 2
            columnSpacing: 8
            rowSpacing: 8
            PropertyField {
                id: gain
                Layout.fillWidth: true
                label: qsTr("增益 dB")
                text: root.shown(root.summary.gainDb, "多值")
            }
            PropertyField {
                id: pan
                Layout.fillWidth: true
                label: qsTr("声像")
                text: root.shown(root.summary.pan, "多值")
            }
            PropertyField {
                id: fadeIn
                Layout.fillWidth: true
                label: qsTr("淡入帧")
                text: root.shown(root.summary.fadeInFrames, "多值")
            }
            PropertyField {
                id: fadeOut
                Layout.fillWidth: true
                label: qsTr("淡出帧")
                text: root.shown(root.summary.fadeOutFrames, "多值")
            }
            PropertyField {
                id: opacity
                Layout.columnSpan: 2
                Layout.fillWidth: true
                label: qsTr("不透明度")
                text: root.shown(root.summary.opacity, "多值")
            }
        }
        AppButton {
            objectName: "applyMultiClipPropertiesButton"
            Layout.fillWidth: true
            Layout.leftMargin: 18
            Layout.rightMargin: 18
            primary: true
            text: qsTr("应用到全部所选片段")
            enabled: mediaflow.workspaceViewController.actionCapabilities.canEdit
                && gain.acceptableInput && pan.acceptableInput
                && fadeIn.acceptableInput && fadeOut.acceptableInput
                && opacity.acceptableInput
            onClicked: mediaflow.timelineClipController.setSelectedClipsProperties(
                Number(gain.text), Number(pan.text), Number(fadeIn.text),
                Number(fadeOut.text), Number(opacity.text))
        }
        RowLayout {
            Layout.fillWidth: true
            Layout.leftMargin: 18
            Layout.rightMargin: 18
            AppButton {
                Layout.fillWidth: true
                text: qsTr("创建复合片段")
                enabled: mediaflow.timelineViewController.canCreateCompoundClip
                onClicked: mediaflow.timelineStructureController.createCompoundClip()
            }
            AppButton {
                Layout.fillWidth: true
                danger: true
                text: qsTr("删除所选")
                enabled: mediaflow.workspaceViewController.actionCapabilities.canEdit
                onClicked: mediaflow.timelineClipController.deleteSelectedClips(false)
            }
        }
        Item { Layout.fillHeight: true; Layout.minimumHeight: 12 }
    }
}

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import ".."

Panel {
    id: root
    objectName: "visualEffectStackPanel"
    property bool canEdit: false
    property var effects: []
    property var effectOptions: []
    implicitHeight: content.implicitHeight + 22

    ColumnLayout {
        id: content
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.margins: 11
        spacing: 8

        RowLayout {
            Layout.fillWidth: true
            Text {
                Layout.fillWidth: true
                text: qsTr("视觉效果")
                color: Theme.text
                font.pixelSize: Theme.fontSizeCaption
                font.weight: Font.DemiBold
            }
            AppComboBox {
                id: effectKind
                objectName: "visualEffectKind"
                Layout.preferredWidth: 148
                textRole: "label"
                valueRole: "value"
                model: root.effectOptions
            }
            AppButton {
                objectName: "addVisualEffectButton"
                text: qsTr("添加")
                compact: true
                enabled: root.canEdit && effectKind.currentValue
                onClicked: mediaflow.timelineEffectsController.addSelectedClipVisualEffect(
                    String(effectKind.currentValue))
            }
        }

        Text {
            Layout.fillWidth: true
            visible: root.effects.length === 0
            text: qsTr("效果按从上到下的顺序进入预览和导出。")
            color: Theme.textMuted
            font.pixelSize: Theme.fontSizeCaption
            wrapMode: Text.WordWrap
        }

        Repeater {
            model: root.effects
            delegate: Rectangle {
                id: effectCard
                required property var modelData
                Layout.fillWidth: true
                implicitHeight: effectContent.implicitHeight + 14
                radius: Theme.radiusSmall
                color: Theme.surfaceRaised
                border.color: Theme.borderSubtle

                ColumnLayout {
                    id: effectContent
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.top: parent.top
                    anchors.margins: 7
                    spacing: 5
                    RowLayout {
                        Layout.fillWidth: true
                        AppSwitch {
                            checked: Boolean(effectCard.modelData.enabled)
                            enabled: root.canEdit
                            onToggled: mediaflow.timelineEffectsController.setSelectedClipVisualEffectEnabled(
                                String(effectCard.modelData.effectId), checked)
                        }
                        Text {
                            Layout.fillWidth: true
                            text: String(effectCard.modelData.label)
                            color: Theme.text
                            font.pixelSize: Theme.fontSizeCaption
                            font.weight: Font.DemiBold
                        }
                        AppIconButton {
                            iconName: "up"
                            enabled: root.canEdit && Number(effectCard.modelData.position) > 0
                            onClicked: mediaflow.timelineEffectsController.moveSelectedClipVisualEffect(
                                String(effectCard.modelData.effectId),
                                Number(effectCard.modelData.position) - 1)
                        }
                        AppIconButton {
                            iconName: "down"
                            enabled: root.canEdit
                                && Number(effectCard.modelData.position) + 1 < root.effects.length
                            onClicked: mediaflow.timelineEffectsController.moveSelectedClipVisualEffect(
                                String(effectCard.modelData.effectId),
                                Number(effectCard.modelData.position) + 1)
                        }
                        AppIconButton {
                            iconName: "delete"
                            danger: true
                            enabled: root.canEdit
                            onClicked: mediaflow.timelineEffectsController.removeSelectedClipVisualEffect(
                                String(effectCard.modelData.effectId))
                        }
                    }
                    Repeater {
                        model: effectCard.modelData.parameterSpecs
                        delegate: EditorFieldControl {
                            id: parameterRow
                            required property var modelData
                            Layout.fillWidth: true
                            enabled: root.canEdit && Boolean(effectCard.modelData.enabled)
                            field: parameterRow.modelData
                            onValueCommitted: value =>
                                mediaflow.timelineEffectsController.setSelectedClipVisualEffectParameter(
                                    String(effectCard.modelData.effectId),
                                    String(parameterRow.modelData.source_id), value)
                        }
                    }
                }
            }
        }
    }
}

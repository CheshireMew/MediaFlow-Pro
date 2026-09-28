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
    property var masks: []
    property int playheadFrame: 0
    signal seekRequested(int frame)
    function maskOptions() {
        const values = [{"label": qsTr("全画面"), "value": ""}];
        for (const mask of root.masks) {
            values.push({"label": String(mask.name), "value": String(mask.maskId)});
        }
        return values;
    }
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
                    RowLayout {
                        Layout.fillWidth: true
                        Text {
                            text: qsTr("作用区域")
                            color: Theme.textMuted
                            font.pixelSize: Theme.fontSizeCaption
                        }
                        AppComboBox {
                            id: effectMask
                            objectName: "visualEffectMask"
                            Layout.fillWidth: true
                            textRole: "label"
                            valueRole: "value"
                            model: root.maskOptions()
                            Component.onCompleted: currentIndex = Math.max(
                                0, indexOfValue(String(effectCard.modelData.maskId || "")))
                            onActivated: mediaflow.timelineMaskController.assignSelectedEffectMask(
                                String(effectCard.modelData.effectId), String(currentValue))
                        }
                    }
                    Repeater {
                        model: effectCard.modelData.parameterSpecs
                        delegate: ColumnLayout {
                            id: parameterRow
                            required property var modelData
                            Layout.fillWidth: true
                            spacing: 4
                            EditorFieldControl {
                                Layout.fillWidth: true
                                enabled: root.canEdit && Boolean(effectCard.modelData.enabled)
                                field: parameterRow.modelData
                                onValueCommitted: value =>
                                    mediaflow.timelineEffectsController.setSelectedClipVisualEffectParameter(
                                        String(effectCard.modelData.effectId),
                                        String(parameterRow.modelData.source_id), value)
                            }
                            RowLayout {
                                Layout.fillWidth: true
                                visible: String(parameterRow.modelData.descriptor.timeline) === "keyframe"
                                Text {
                                    Layout.fillWidth: true
                                    text: qsTr("%1 个参数关键帧").arg(
                                        (parameterRow.modelData.keyframes || []).length)
                                    color: Theme.textMuted
                                    font.pixelSize: Theme.fontSizeCaption
                                }
                                AppButton {
                                    text: qsTr("在播放头保存")
                                    compact: true
                                    enabled: root.canEdit && Boolean(effectCard.modelData.enabled)
                                        && root.playheadFrame >= Number(mediaflow.timelineViewController.selectedClipData.startFrame || 0)
                                        && root.playheadFrame < Number(mediaflow.timelineViewController.selectedClipData.endFrame || 0)
                                    onClicked: mediaflow.timelineKeyframeController.setEffectParameterKeyframe(
                                        String(effectCard.modelData.effectId),
                                        String(parameterRow.modelData.source_id),
                                        root.playheadFrame, Number(parameterRow.modelData.value),
                                        "linear", 0.25, 0.1, 0.25, 1)
                                }
                            }
                            RowLayout {
                                Layout.fillWidth: true
                                visible: (parameterRow.modelData.keyframes || []).length > 0
                                AppButton {
                                    text: qsTr("复制全部")
                                    compact: true
                                    onClicked: mediaflow.timelineKeyframeController.copyEffectParameterKeyframes(
                                        String(effectCard.modelData.effectId),
                                        String(parameterRow.modelData.source_id),
                                        (parameterRow.modelData.keyframes || []).map(
                                            item => Number(item.timelineOffset)))
                                }
                                AppButton {
                                    text: qsTr("粘贴到播放头")
                                    compact: true
                                    enabled: root.canEdit
                                    onClicked: mediaflow.timelineKeyframeController.pasteEffectParameterKeyframes(
                                        String(effectCard.modelData.effectId),
                                        String(parameterRow.modelData.source_id),
                                        root.playheadFrame, Number(effectPasteScale.text))
                                }
                                PropertyField {
                                    id: effectPasteScale
                                    Layout.preferredWidth: 78
                                    label: qsTr("时间倍率")
                                    text: "1"
                                }
                            }
                            RowLayout {
                                Layout.fillWidth: true
                                visible: (parameterRow.modelData.keyframes || []).length > 1
                                PropertyField {
                                    id: effectRetimeScale
                                    Layout.fillWidth: true
                                    label: qsTr("全部时间缩放")
                                    text: "1"
                                }
                                AppButton {
                                    text: qsTr("应用")
                                    compact: true
                                    enabled: root.canEdit
                                    onClicked: {
                                        const values = parameterRow.modelData.keyframes || [];
                                        const offsets = values.map(item => Number(item.timelineOffset));
                                        mediaflow.timelineKeyframeController.retimeEffectParameterKeyframes(
                                            String(effectCard.modelData.effectId),
                                            String(parameterRow.modelData.source_id), offsets,
                                            offsets[0], Number(effectRetimeScale.text));
                                    }
                                }
                            }
                            Repeater {
                                model: parameterRow.modelData.keyframes || []
                                delegate: RowLayout {
                                    id: effectKeyframeRow
                                    required property var modelData
                                    Layout.fillWidth: true
                                    Text {
                                        Layout.preferredWidth: 104
                                        text: qsTr("片段帧 %1 · %2").arg(
                                            effectKeyframeRow.modelData.timelineOffset).arg(
                                            Number(effectKeyframeRow.modelData.value).toFixed(3))
                                        color: Theme.textSubtle
                                        font.pixelSize: Theme.fontSizeCaption
                                        font.family: Theme.monoFontFamily
                                    }
                                    AppComboBox {
                                        Layout.fillWidth: true
                                        textRole: "label"
                                        valueRole: "value"
                                        model: mediaflow.timelineKeyframeController.interpolationOptions
                                        Component.onCompleted: currentIndex = Math.max(0,
                                            indexOfValue(String((effectKeyframeRow.modelData.curve || {}).interpolation || "linear")))
                                        onActivated: {
                                            const curve = effectKeyframeRow.modelData.curve || {};
                                            mediaflow.timelineKeyframeController.setEffectParameterKeyframe(
                                                String(effectCard.modelData.effectId),
                                                String(parameterRow.modelData.source_id),
                                                Number(effectKeyframeRow.modelData.timelineFrame),
                                                Number(effectKeyframeRow.modelData.value),
                                                String(currentValue), Number(curve.x1 ?? 0.25),
                                                Number(curve.y1 ?? 0.1), Number(curve.x2 ?? 0.25),
                                                Number(curve.y2 ?? 1));
                                        }
                                    }
                                    AppIconButton {
                                        iconName: "minus"
                                        enabled: root.canEdit
                                            && Number(effectKeyframeRow.modelData.timelineOffset) > 0
                                        onClicked: mediaflow.timelineKeyframeController.moveEffectParameterKeyframe(
                                            String(effectCard.modelData.effectId),
                                            String(parameterRow.modelData.source_id),
                                            Number(effectKeyframeRow.modelData.timelineOffset),
                                            Number(effectKeyframeRow.modelData.timelineOffset) - 1)
                                        toolTipText: qsTr("向前移动一帧")
                                    }
                                    AppIconButton {
                                        iconName: "add"
                                        enabled: root.canEdit
                                            && Number(effectKeyframeRow.modelData.timelineOffset) + 1
                                                < Number(mediaflow.timelineViewController.selectedClipData.durationFrames || 0)
                                        onClicked: mediaflow.timelineKeyframeController.moveEffectParameterKeyframe(
                                            String(effectCard.modelData.effectId),
                                            String(parameterRow.modelData.source_id),
                                            Number(effectKeyframeRow.modelData.timelineOffset),
                                            Number(effectKeyframeRow.modelData.timelineOffset) + 1)
                                        toolTipText: qsTr("向后移动一帧")
                                    }
                                    AppIconButton {
                                        iconName: "play"
                                        onClicked: root.seekRequested(
                                            Number(effectKeyframeRow.modelData.timelineFrame))
                                        toolTipText: qsTr("跳到关键帧")
                                    }
                                    AppIconButton {
                                        iconName: "delete"
                                        danger: true
                                        enabled: root.canEdit
                                        onClicked: mediaflow.timelineKeyframeController.removeEffectParameterKeyframe(
                                            String(effectCard.modelData.effectId),
                                            String(parameterRow.modelData.source_id),
                                            Number(effectKeyframeRow.modelData.timelineOffset))
                                        toolTipText: qsTr("移除参数关键帧")
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}

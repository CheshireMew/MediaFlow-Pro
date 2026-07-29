import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "."
import "components"

AppScrollView {
    id: editScroll
    objectName: "editPanel"
    clip: true
    contentWidth: availableWidth
    property int playheadFrame: 0

    ColumnLayout {
        id: root
        width: editScroll.availableWidth
        spacing: 10
        property var transitionOptions: timelineController.transitionOptions
        property string loadedTransitionId: ""
        readonly property bool canEdit: Boolean(workspaceController.actionCapabilities.canEdit)

        function loadSelectedTransition() {
            const transitionId = String(timelineController.selectedTransitionId || "");
            if (transitionId.length === 0) {
                loadedTransitionId = "";
                return;
            }
            if (transitionId === loadedTransitionId
                    && (transitionKind.activeFocus || transitionDuration.activeFocus))
                return;
            const data = timelineController.selectedTransitionData;
            transitionKind.currentIndex = Math.max(
                0, transitionKind.indexOfValue(String(data.kind || "dissolve")));
            transitionDuration.value = Number(data.durationFrames || 15);
            loadedTransitionId = transitionId;
        }

        Component.onCompleted: Qt.callLater(loadSelectedTransition)

        Connections {
            target: timelineController
            function onSelectionChanged() { root.loadSelectedTransition(); }
            function onHistoryChanged() { root.loadSelectedTransition(); }
        }

        WebLayerPanel { playheadFrame: editScroll.playheadFrame }

        Panel {
            objectName: "editCompoundClipPanel"
            Layout.fillWidth: true
            implicitHeight: compoundContent.implicitHeight + 22
            visible: timelineController.selectedCompoundId.length > 0
            enabled: root.canEdit
            ColumnLayout {
                id: compoundContent
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.top: parent.top
                anchors.margins: 11
                spacing: 8
                Text {
                    Layout.fillWidth: true
                    text: timelineController.selectedCompoundData.name || qsTr("复合片段")
                    color: Theme.text
                    font.pixelSize: Theme.fontSizeBodySmall
                    font.weight: Font.DemiBold
                }
                Text {
                    Layout.fillWidth: true
                    text: qsTr("包含 %1 个片段，共 %2 帧。它会作为一个整体移动和删除，预览与导出仍使用原始素材。")
                        .arg(timelineController.selectedCompoundData.memberCount || 0)
                        .arg(timelineController.selectedCompoundData.durationFrames || 0)
                    color: Theme.textMuted
                    font.pixelSize: Theme.fontSizeCaption
                    wrapMode: Text.WordWrap
                }
                RowLayout {
                    Layout.fillWidth: true
                    AppButton {
                        Layout.fillWidth: true
                        text: qsTr("解除复合")
                        onClicked: timelineController.dissolveSelectedCompoundClip()
                    }
                    AppButton {
                        Layout.fillWidth: true
                        danger: true
                        text: qsTr("删除复合片段")
                        onClicked: timelineController.deleteSelectedClips(false)
                    }
                }
            }
        }

        Panel {
            objectName: "editClipTimingPanel"
            Layout.fillWidth: true
            implicitHeight: timingContent.implicitHeight + 22
            visible: timelineController.selectedClipId.length > 0 && !webController.isWebClip
            enabled: root.canEdit
            ColumnLayout {
                id: timingContent
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.top: parent.top
                anchors.margins: 11
                spacing: 7
                Text {
                    Layout.fillWidth: true
                    text: timelineController.selectedClipData.assetName || qsTr("时间线片段")
                    color: Theme.text
                    font.pixelSize: Theme.fontSizeBodySmall
                    font.weight: Font.DemiBold
                    elide: Text.ElideRight
                }
                Text {
                    text: qsTr("裁剪与速度")
                    color: Theme.textMuted
                    font.pixelSize: Theme.fontSizeCaption
                }
                PropertyField {
                    id: sourceIn
                    Layout.fillWidth: true
                    label: qsTr("源入点")
                    text: String(timelineController.selectedClipData.sourceIn ?? 0)
                }
                PropertyField {
                    id: clipDuration
                    Layout.fillWidth: true
                    label: qsTr("持续帧")
                    text: String(timelineController.selectedClipData.durationFrames ?? 1)
                }
                RowLayout {
                    Layout.fillWidth: true
                    AppComboBox {
                        id: speed
                        Layout.fillWidth: true
                        model: [-4, -2, -1, -0.5, -0.25, 0.25, 0.5, 1, 1.5, 2, 4]
                        currentIndex: Math.max(0, model.indexOf(timelineController.selectedClipData.speed ?? 1))
                    }
                    AppSwitch {
                        id: pitch
                        text: qsTr("保音高")
                        checked: timelineController.selectedClipData.pitchCompensation ?? true
                    }
                }
                RowLayout {
                    Layout.fillWidth: true
                    AppButton {
                        Layout.fillWidth: true
                        text: qsTr("应用裁剪")
                        onClicked: timelineController.trimClip(timelineController.selectedClipId, Number(sourceIn.text), Number(clipDuration.text))
                    }
                    AppButton {
                        Layout.fillWidth: true
                        text: qsTr("应用速度")
                        onClicked: timelineController.setClipSpeed(timelineController.selectedClipId, Number(speed.currentValue), pitch.checked)
                    }
                }
                AppButton {
                    objectName: "detachClipAudioButton"
                    Layout.fillWidth: true
                    visible: timelineController.selectedClipData.canDetachAudio === true
                    text: qsTr("解除视音频绑定")
                    onClicked: timelineController.detachClipAudio(timelineController.selectedClipId)
                }
            }
        }

        Panel {
            objectName: "editClipTransformPanel"
            Layout.fillWidth: true
            implicitHeight: transformContent.implicitHeight + 22
            visible: timelineController.selectedClipId.length > 0 && !webController.isWebClip
            enabled: root.canEdit
            ColumnLayout {
                id: transformContent
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.top: parent.top
                anchors.margins: 11
                spacing: 6
                Text {
                    text: qsTr("画面变换")
                    color: Theme.text
                    font.pixelSize: Theme.fontSizeCaption
                    font.weight: Font.DemiBold
                }
                GridLayout {
                    Layout.fillWidth: true
                    columns: 2
                    columnSpacing: 7
                    rowSpacing: 6
                    PropertyField {
                        id: posX
                        objectName: "editClipPosX"
                        Layout.fillWidth: true
                        label: "X %"
                        text: String(timelineController.selectedClipData.x ?? 0)
                    }
                    PropertyField {
                        id: posY
                        objectName: "editClipPosY"
                        Layout.fillWidth: true
                        label: "Y %"
                        text: String(timelineController.selectedClipData.y ?? 0)
                    }
                    PropertyField {
                        id: scaleX
                        Layout.fillWidth: true
                        label: qsTr("横向缩放")
                        text: String(timelineController.selectedClipData.scaleX ?? 1)
                    }
                    PropertyField {
                        id: scaleY
                        Layout.fillWidth: true
                        label: qsTr("纵向缩放")
                        text: String(timelineController.selectedClipData.scaleY ?? 1)
                    }
                    PropertyField {
                        id: rotation
                        Layout.fillWidth: true
                        label: qsTr("旋转 °")
                        text: String(timelineController.selectedClipData.rotation ?? 0)
                    }
                    PropertyField {
                        id: opacity
                        Layout.fillWidth: true
                        label: qsTr("透明度")
                        text: String(timelineController.selectedClipData.opacity ?? 1)
                    }
                }
                Text {
                    text: qsTr("裁剪")
                    color: Theme.textMuted
                    font.pixelSize: Theme.fontSizeCaption
                }
                GridLayout {
                    Layout.fillWidth: true
                    columns: 2
                    columnSpacing: 7
                    rowSpacing: 6
                    PropertyField {
                        id: cropLeft
                        Layout.fillWidth: true
                        label: qsTr("左")
                        text: String(timelineController.selectedClipData.cropLeft ?? 0)
                    }
                    PropertyField {
                        id: cropTop
                        Layout.fillWidth: true
                        label: qsTr("上")
                        text: String(timelineController.selectedClipData.cropTop ?? 0)
                    }
                    PropertyField {
                        id: cropRight
                        Layout.fillWidth: true
                        label: qsTr("右")
                        text: String(timelineController.selectedClipData.cropRight ?? 0)
                    }
                    PropertyField {
                        id: cropBottom
                        Layout.fillWidth: true
                        label: qsTr("下")
                        text: String(timelineController.selectedClipData.cropBottom ?? 0)
                    }
                }
                AppButton {
                    objectName: "applyClipTransformButton"
                    Layout.fillWidth: true
                    primary: true
                    text: qsTr("应用画面参数")
                    onClicked: timelineController.setClipTransform(timelineController.selectedClipId, Number(posX.text), Number(posY.text), Number(scaleX.text), Number(scaleY.text), Number(rotation.text), Number(cropLeft.text), Number(cropTop.text), Number(cropRight.text), Number(cropBottom.text), Number(opacity.text))
                }
                Text {
                    Layout.fillWidth: true
                    text: Number(timelineController.selectedClipData.transformKeyframeCount || 0) > 0
                          ? qsTr("已有 %1 个画面关键帧").arg(timelineController.selectedClipData.transformKeyframeCount)
                          : qsTr("可对视频分析场景、自动构图或跟踪主体")
                    color: Theme.textMuted
                    font.pixelSize: Theme.fontSizeCaption
                    wrapMode: Text.WordWrap
                }
                RowLayout {
                    Layout.fillWidth: true
                    AppButton {
                        objectName: "detectScenesButton"
                        Layout.fillWidth: true
                        text: qsTr("检测场景")
                        onClicked: timelineController.detectScenesSelected(0.35)
                    }
                    AppButton {
                        objectName: "autoReframeButton"
                        Layout.fillWidth: true
                        text: qsTr("自动构图")
                        onClicked: timelineController.autoReframeSelected()
                    }
                    AppButton {
                        objectName: "trackSubjectButton"
                        Layout.fillWidth: true
                        text: qsTr("主体跟踪")
                        onClicked: timelineController.trackSubjectSelected()
                    }
                }
                RowLayout {
                    Layout.fillWidth: true
                    AppButton {
                        Layout.fillWidth: true
                        danger: true
                        text: qsTr("删除所选")
                        onClicked: timelineController.deleteSelectedClips(false)
                    }
                    AppButton {
                        Layout.fillWidth: true
                        text: qsTr("波纹删除")
                        onClicked: timelineController.deleteSelectedClips(true)
                    }
                }
            }
        }

        Text {
            text: qsTr("视觉资源 · 转场")
            color: Theme.text
            font.pixelSize: Theme.fontSizeBodySmall
            font.weight: Font.DemiBold
        }
        Text {
            Layout.fillWidth: true
            text: qsTr("选择一个片段，在它与同轨道下一个相邻片段之间创建转场。")
            color: Theme.textMuted
            font.pixelSize: Theme.fontSizeCaption
            wrapMode: Text.WordWrap
        }
        GridLayout {
            Layout.fillWidth: true
            columns: 2
            columnSpacing: 7
            rowSpacing: 7
            Repeater {
                model: root.transitionOptions
                Rectangle {
                    id: resourceCard
                    required property var modelData
                    Layout.fillWidth: true
                    Layout.preferredHeight: 104
                    radius: Theme.radiusSmall
                    color: resourceMouse.containsMouse ? Theme.surfaceHover : Theme.surfaceRaised
                    border.color: activeFocus || resourceMouse.containsMouse ? Theme.accent : Theme.border
                    border.width: activeFocus ? 2 : 1
                    enabled: root.canEdit
                        && timelineController.selectedClipId.length > 0
                    opacity: enabled ? 1 : 0.55
                    activeFocusOnTab: true
                    function activateResource() {
                        if (root.canEdit && timelineController.selectedClipId.length > 0)
                            timelineController.addTransitionAfter(
                                timelineController.selectedClipId,
                                modelData.value,
                                Number(modelData.defaultDurationFrames));
                    }

                    ColumnLayout {
                        anchors.fill: parent
                        anchors.margins: 9
                        spacing: 5
                        Rectangle {
                            Layout.fillWidth: true
                            Layout.preferredHeight: 31
                            radius: 4
                            color: Theme.surface
                            clip: true
                            Rectangle {
                                anchors.top: parent.top
                                anchors.bottom: parent.bottom
                                width: resourceMouse.containsMouse
                                    ? parent.width * 0.64 : parent.width * 0.38
                                x: modelData.previewDirection === "right"
                                    ? parent.width - width : 0
                                color: Theme.accentSoft
                                Behavior on width { NumberAnimation { duration: 220 } }
                            }
                            AppIcon {
                                anchors.centerIn: parent
                                width: 18
                                height: 18
                                iconName: modelData.previewDirection === "zoom"
                                    ? "transition-zoom"
                                    : modelData.previewDirection === "black"
                                    ? "transition-black"
                                    : modelData.previewDirection === "left"
                                    ? "chevron-left"
                                    : modelData.previewDirection === "right"
                                    ? "chevron-right" : "transition"
                                iconColor: Theme.textSubtle
                            }
                        }
                        Text {
                            Layout.fillWidth: true
                            text: modelData.label
                            color: Theme.text
                            font.pixelSize: Theme.fontSizeBodySmall
                            font.weight: Font.DemiBold
                            elide: Text.ElideRight
                        }
                        Text {
                            Layout.fillWidth: true
                            text: modelData.description
                            color: Theme.textMuted
                            font.pixelSize: Theme.fontSizeCaption
                            maximumLineCount: 2
                            elide: Text.ElideRight
                            wrapMode: Text.WordWrap
                        }
                    }
                    Accessible.name: modelData.label
                    Accessible.description: modelData.description
                    Accessible.role: Accessible.Button
                    Keys.onReturnPressed: function (event) {
                        resourceCard.activateResource();
                        event.accepted = true;
                    }
                    Keys.onSpacePressed: function (event) {
                        resourceCard.activateResource();
                        event.accepted = true;
                    }
                    MouseArea {
                        id: resourceMouse
                        anchors.fill: parent
                        hoverEnabled: true
                        enabled: resourceCard.enabled
                        onEntered: timelineController.previewTransitionAfter(
                            timelineController.selectedClipId,
                            modelData.value,
                            Number(modelData.defaultDurationFrames))
                        onExited: timelineController.clearTransitionPreview()
                        onClicked: timelineController.addTransitionAfter(
                            timelineController.selectedClipId,
                            modelData.value,
                            Number(modelData.defaultDurationFrames))
                    }
                }
            }
        }
        Panel {
            Layout.fillWidth: true
            implicitHeight: 176
            visible: timelineController.selectedTransitionId.length > 0
            enabled: root.canEdit
            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 10
                spacing: 7
                Text {
                    text: qsTr("调整所选转场")
                    color: Theme.text
                    font.pixelSize: Theme.fontSizeCaption
                    font.weight: Font.DemiBold
                }
                AppComboBox {
                    id: transitionKind
                    objectName: "selectedTransitionKind"
                    Layout.fillWidth: true
                    textRole: "label"
                    valueRole: "value"
                    model: root.transitionOptions
                }
                AppSpinBox {
                    id: transitionDuration
                    objectName: "selectedTransitionDuration"
                    Layout.fillWidth: true
                    from: 1
                    to: 300
                    editable: true
                }
                RowLayout {
                    Layout.fillWidth: true
                    AppButton {
                        Layout.fillWidth: true
                        primary: true
                        text: qsTr("应用")
                        onClicked: timelineController.updateTransition(timelineController.selectedTransitionId, transitionKind.currentValue, transitionDuration.value)
                    }
                    AppButton {
                        Layout.fillWidth: true
                        danger: true
                        text: qsTr("移除转场")
                        onClicked: timelineController.removeTransition(timelineController.selectedTransitionId)
                    }
                }
            }
        }
        EmptyState {
            Layout.fillWidth: true
            Layout.preferredHeight: 260
            visible: timelineController.selectedClipId.length === 0
                && timelineController.selectedCompoundId.length === 0
                && timelineController.selectedTransitionId.length === 0
            iconName: "cut"
            title: qsTr("选择时间线片段")
            description: qsTr("选择后可以编辑画面、裁剪速度并添加转场。")
        }
    }
}

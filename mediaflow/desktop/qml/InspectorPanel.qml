import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Dialogs
import "."
import "components"

ColumnLayout {
    id: root
    spacing: 12
    FileDialog {
        id: relinkDialog
        title: qsTr("重新定位离线素材")
        fileMode: FileDialog.OpenFile
        onAccepted: mediaController.relinkMedia(mediaController.selectedAssetId, selectedFile.toString())
    }
    Dialog {
        id: replaceDialog
        anchors.centerIn: parent
        implicitWidth: 400
        width: 400
        modal: true
        title: qsTr("替换为不同内容？")
        standardButtons: Dialog.Yes | Dialog.No
        onAccepted: mediaController.resolveRelinkReplacement(true)
        onRejected: mediaController.resolveRelinkReplacement(false)
        contentItem: Text {
            width: 360
            text: qsTr("所选文件的内容指纹与原素材不同：\n%1\n\n只有确认替换后才会关联，相关代理与波形会失效。").arg(workspaceController.pendingRelinkPath)
            color: Theme.text
            wrapMode: Text.WordWrap
        }
    }
    Connections {
        target: workspaceController
        function onRelinkConfirmationChanged() {
            if (workspaceController.relinkConfirmationPending)
                replaceDialog.open()
            else
                replaceDialog.close()
        }
    }
    Text {
        text: qsTr("检查器")
        color: Theme.text
        font.pixelSize: Theme.fontSizeSection
        font.weight: Font.DemiBold
    }

    ScrollView {
        Layout.fillWidth: true
        Layout.fillHeight: true
        clip: true
        contentWidth: availableWidth

        ColumnLayout {
            width: parent.width
            spacing: 10

            Panel {
                Layout.fillWidth: true
                implicitHeight: mediaController.selectedAssetData.status === "offline" ? 208 : 172
                visible: mediaController.selectedAssetId.length > 0 && timelineController.selectedClipId.length === 0
                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 12
                    spacing: 8
                    Text { text: qsTr("素材"); color: Theme.textMuted; font.pixelSize: Theme.fontSizeCaption }
                    Text { Layout.fillWidth: true; text: mediaController.selectedAssetData.name || ""; color: Theme.text; font.pixelSize: Theme.fontSizeBodySmall; font.weight: Font.DemiBold; elide: Text.ElideRight }
                    Text { Layout.fillWidth: true; text: mediaController.selectedAssetData.path || ""; color: Theme.textMuted; font.pixelSize: Theme.fontSizeCaption; elide: Text.ElideMiddle }
                    AppButton {
                        Layout.fillWidth: true
                        visible: mediaController.selectedAssetData.status === "offline"
                        text: qsTr("重新定位素材")
                        primary: true
                        onClicked: relinkDialog.open()
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        AppButton { Layout.fillWidth: true; text: qsTr("生成代理"); onClicked: mediaController.generateProxy(mediaController.selectedAssetId) }
                        AppButton { Layout.fillWidth: true; text: qsTr("生成波形"); onClicked: mediaController.generateWaveform(mediaController.selectedAssetId) }
                    }
                    AppButton { Layout.fillWidth: true; primary: true; text: qsTr("添加到时间线"); onClicked: mediaController.addAssetToTimeline(mediaController.selectedAssetId) }
                }
            }

            ColumnLayout {
                Layout.fillWidth: true
                visible: timelineController.selectedClipId.length > 0
                spacing: 10

                Panel {
                    Layout.fillWidth: true
                    implicitHeight: 174
                    ColumnLayout {
                        anchors.fill: parent; anchors.margins: 11; spacing: 6
                        Text { Layout.fillWidth: true; text: timelineController.selectedClipData.assetName || qsTr("时间线片段"); color: Theme.text; font.pixelSize: Theme.fontSizeBodySmall; font.weight: Font.DemiBold; elide: Text.ElideRight }
                        Text { text: qsTr("裁剪与速度"); color: Theme.textMuted; font.pixelSize: Theme.fontSizeCaption }
                        InspectorField { id: sourceIn; Layout.fillWidth: true; label: qsTr("源入点"); text: String(timelineController.selectedClipData.sourceIn ?? 0) }
                        InspectorField { id: clipDuration; Layout.fillWidth: true; label: qsTr("持续帧"); text: String(timelineController.selectedClipData.durationFrames ?? 1) }
                        RowLayout {
                            Layout.fillWidth: true
                            AppComboBox {
                                id: speed
                                Layout.fillWidth: true
                                model: [-4, -2, -1, -0.5, -0.25, 0.25, 0.5, 1, 1.5, 2, 4]
                                currentIndex: Math.max(0, model.indexOf(timelineController.selectedClipData.speed ?? 1))
                            }
                            Switch { id: pitch; text: qsTr("保音高"); checked: timelineController.selectedClipData.pitchCompensation ?? true }
                        }
                        RowLayout {
                            Layout.fillWidth: true
                            AppButton { Layout.fillWidth: true; text: qsTr("应用裁剪"); onClicked: timelineController.trimClip(timelineController.selectedClipId, Number(sourceIn.text), Number(clipDuration.text)) }
                            AppButton { Layout.fillWidth: true; text: qsTr("应用速度"); onClicked: timelineController.setClipSpeed(timelineController.selectedClipId, Number(speed.currentValue), pitch.checked) }
                        }
                    }
                }

                Panel {
                    Layout.fillWidth: true
                    implicitHeight: 324
                    ColumnLayout {
                        anchors.fill: parent; anchors.margins: 11; spacing: 5
                        Text { text: qsTr("画面变换"); color: Theme.text; font.pixelSize: Theme.fontSizeCaption; font.weight: Font.DemiBold }
                        InspectorField { id: posX; Layout.fillWidth: true; label: "X %"; text: String(timelineController.selectedClipData.x ?? 0) }
                        InspectorField { id: posY; Layout.fillWidth: true; label: "Y %"; text: String(timelineController.selectedClipData.y ?? 0) }
                        InspectorField { id: scaleX; Layout.fillWidth: true; label: qsTr("横向缩放"); text: String(timelineController.selectedClipData.scaleX ?? 1) }
                        InspectorField { id: scaleY; Layout.fillWidth: true; label: qsTr("纵向缩放"); text: String(timelineController.selectedClipData.scaleY ?? 1) }
                        InspectorField { id: rotation; Layout.fillWidth: true; label: qsTr("旋转 °"); text: String(timelineController.selectedClipData.rotation ?? 0) }
                        InspectorField { id: opacity; Layout.fillWidth: true; label: qsTr("透明度"); text: String(timelineController.selectedClipData.opacity ?? 1) }
                        GridLayout {
                            Layout.fillWidth: true; columns: 2; columnSpacing: 6; rowSpacing: 5
                            InspectorField { id: cropLeft; Layout.fillWidth: true; label: qsTr("裁左"); text: String(timelineController.selectedClipData.cropLeft ?? 0) }
                            InspectorField { id: cropTop; Layout.fillWidth: true; label: qsTr("裁上"); text: String(timelineController.selectedClipData.cropTop ?? 0) }
                            InspectorField { id: cropRight; Layout.fillWidth: true; label: qsTr("裁右"); text: String(timelineController.selectedClipData.cropRight ?? 0) }
                            InspectorField { id: cropBottom; Layout.fillWidth: true; label: qsTr("裁下"); text: String(timelineController.selectedClipData.cropBottom ?? 0) }
                        }
                        AppButton {
                            Layout.fillWidth: true; primary: true; text: qsTr("应用画面参数")
                            onClicked: timelineController.setClipTransform(
                                timelineController.selectedClipId,
                                Number(posX.text), Number(posY.text), Number(scaleX.text), Number(scaleY.text),
                                Number(rotation.text), Number(cropLeft.text), Number(cropTop.text),
                                Number(cropRight.text), Number(cropBottom.text), Number(opacity.text))
                        }
                    }
                }

                Panel {
                    Layout.fillWidth: true
                    implicitHeight: 224
                    ColumnLayout {
                        anchors.fill: parent; anchors.margins: 11; spacing: 6
                        Text { text: qsTr("片段音频"); color: Theme.text; font.pixelSize: Theme.fontSizeCaption; font.weight: Font.DemiBold }
                        InspectorField { id: gain; Layout.fillWidth: true; label: qsTr("增益 dB"); text: String(timelineController.selectedClipData.gainDb ?? 0) }
                        InspectorField { id: pan; Layout.fillWidth: true; label: qsTr("声像"); text: String(timelineController.selectedClipData.pan ?? 0) }
                        InspectorField { id: fadeIn; Layout.fillWidth: true; label: qsTr("淡入帧"); text: String(timelineController.selectedClipData.fadeInFrames ?? 0) }
                        InspectorField { id: fadeOut; Layout.fillWidth: true; label: qsTr("淡出帧"); text: String(timelineController.selectedClipData.fadeOutFrames ?? 0) }
                        AppButton {
                            Layout.fillWidth: true; primary: true; text: qsTr("应用音频参数")
                            onClicked: timelineController.setClipAudio(
                                timelineController.selectedClipId, Number(gain.text), Number(pan.text),
                                Number(fadeIn.text), Number(fadeOut.text))
                        }
                    }
                }

                RowLayout {
                    Layout.fillWidth: true
                    AppButton { Layout.fillWidth: true; text: qsTr("删除所选"); danger: true; onClicked: timelineController.deleteSelectedClips(false) }
                    AppButton { Layout.fillWidth: true; text: qsTr("波纹删除所选"); onClicked: timelineController.deleteSelectedClips(true) }
                }
            }

            EmptyState {
                Layout.fillWidth: true
                Layout.preferredHeight: 360
                visible: mediaController.selectedAssetId.length === 0 && timelineController.selectedClipId.length === 0
                iconText: "≡"
                title: qsTr("这里空空如也")
                description: qsTr("选择素材、片段、轨道、字幕或音频总线后，可在这里编辑属性。")
            }
        }
    }

}

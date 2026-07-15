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
        onAccepted: projectController.relinkMedia(projectController.selectedAssetId, selectedFile.toString())
    }
    Dialog {
        id: replaceDialog
        anchors.centerIn: parent
        implicitWidth: 400
        width: 400
        modal: true
        title: qsTr("替换为不同内容？")
        standardButtons: Dialog.Yes | Dialog.No
        onAccepted: projectController.resolveRelinkReplacement(true)
        onRejected: projectController.resolveRelinkReplacement(false)
        contentItem: Text {
            width: 360
            text: qsTr("所选文件的内容指纹与原素材不同：\n%1\n\n只有确认替换后才会关联，相关代理与波形会失效。").arg(projectController.pendingRelinkPath)
            color: Theme.text
            wrapMode: Text.WordWrap
        }
    }
    Connections {
        target: projectController
        function onRelinkConfirmationChanged() {
            if (projectController.relinkConfirmationPending)
                replaceDialog.open()
            else
                replaceDialog.close()
        }
    }
    Text {
        text: qsTr("检查器")
        color: Theme.text
        font.pixelSize: 16
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
                implicitHeight: projectController.selectedAssetData.status === "offline" ? 208 : 172
                visible: projectController.selectedAssetId.length > 0 && projectController.selectedClipId.length === 0
                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 12
                    spacing: 8
                    Text { text: qsTr("素材"); color: Theme.textMuted; font.pixelSize: 11 }
                    Text { Layout.fillWidth: true; text: projectController.selectedAssetData.name || ""; color: Theme.text; font.pixelSize: 12; font.weight: Font.DemiBold; elide: Text.ElideRight }
                    Text { Layout.fillWidth: true; text: projectController.selectedAssetData.path || ""; color: Theme.textMuted; font.pixelSize: 9; elide: Text.ElideMiddle }
                    AppButton {
                        Layout.fillWidth: true
                        visible: projectController.selectedAssetData.status === "offline"
                        text: qsTr("重新定位素材")
                        primary: true
                        onClicked: relinkDialog.open()
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        AppButton { Layout.fillWidth: true; text: qsTr("生成代理"); onClicked: projectController.generateProxy(projectController.selectedAssetId) }
                        AppButton { Layout.fillWidth: true; text: qsTr("生成波形"); onClicked: projectController.generateWaveform(projectController.selectedAssetId) }
                    }
                    AppButton { Layout.fillWidth: true; primary: true; text: qsTr("添加到时间线"); onClicked: projectController.addAssetToTimeline(projectController.selectedAssetId) }
                }
            }

            ColumnLayout {
                Layout.fillWidth: true
                visible: projectController.selectedClipId.length > 0
                spacing: 10

                Panel {
                    Layout.fillWidth: true
                    implicitHeight: 174
                    ColumnLayout {
                        anchors.fill: parent; anchors.margins: 11; spacing: 6
                        Text { Layout.fillWidth: true; text: projectController.selectedClipData.assetName || qsTr("时间线片段"); color: Theme.text; font.pixelSize: 12; font.weight: Font.DemiBold; elide: Text.ElideRight }
                        Text { text: qsTr("裁剪与速度"); color: Theme.textMuted; font.pixelSize: 10 }
                        InspectorField { id: sourceIn; Layout.fillWidth: true; label: qsTr("源入点"); text: String(projectController.selectedClipData.sourceIn ?? 0) }
                        InspectorField { id: clipDuration; Layout.fillWidth: true; label: qsTr("持续帧"); text: String(projectController.selectedClipData.durationFrames ?? 1) }
                        RowLayout {
                            Layout.fillWidth: true
                            ComboBox {
                                id: speed
                                Layout.fillWidth: true
                                model: [-4, -2, -1, -0.5, -0.25, 0.25, 0.5, 1, 1.5, 2, 4]
                                currentIndex: Math.max(0, model.indexOf(projectController.selectedClipData.speed ?? 1))
                            }
                            Switch { id: pitch; text: qsTr("保音高"); checked: projectController.selectedClipData.pitchCompensation ?? true }
                        }
                        RowLayout {
                            Layout.fillWidth: true
                            AppButton { Layout.fillWidth: true; text: qsTr("应用裁剪"); onClicked: projectController.trimClip(projectController.selectedClipId, Number(sourceIn.text), Number(clipDuration.text)) }
                            AppButton { Layout.fillWidth: true; text: qsTr("应用速度"); onClicked: projectController.setClipSpeed(projectController.selectedClipId, Number(speed.currentValue), pitch.checked) }
                        }
                    }
                }

                Panel {
                    Layout.fillWidth: true
                    implicitHeight: 324
                    ColumnLayout {
                        anchors.fill: parent; anchors.margins: 11; spacing: 5
                        Text { text: qsTr("画面变换"); color: Theme.text; font.pixelSize: 11; font.weight: Font.DemiBold }
                        InspectorField { id: posX; Layout.fillWidth: true; label: "X %"; text: String(projectController.selectedClipData.x ?? 0) }
                        InspectorField { id: posY; Layout.fillWidth: true; label: "Y %"; text: String(projectController.selectedClipData.y ?? 0) }
                        InspectorField { id: scaleX; Layout.fillWidth: true; label: qsTr("横向缩放"); text: String(projectController.selectedClipData.scaleX ?? 1) }
                        InspectorField { id: scaleY; Layout.fillWidth: true; label: qsTr("纵向缩放"); text: String(projectController.selectedClipData.scaleY ?? 1) }
                        InspectorField { id: rotation; Layout.fillWidth: true; label: qsTr("旋转 °"); text: String(projectController.selectedClipData.rotation ?? 0) }
                        InspectorField { id: opacity; Layout.fillWidth: true; label: qsTr("透明度"); text: String(projectController.selectedClipData.opacity ?? 1) }
                        GridLayout {
                            Layout.fillWidth: true; columns: 2; columnSpacing: 6; rowSpacing: 5
                            InspectorField { id: cropLeft; Layout.fillWidth: true; label: qsTr("裁左"); text: String(projectController.selectedClipData.cropLeft ?? 0) }
                            InspectorField { id: cropTop; Layout.fillWidth: true; label: qsTr("裁上"); text: String(projectController.selectedClipData.cropTop ?? 0) }
                            InspectorField { id: cropRight; Layout.fillWidth: true; label: qsTr("裁右"); text: String(projectController.selectedClipData.cropRight ?? 0) }
                            InspectorField { id: cropBottom; Layout.fillWidth: true; label: qsTr("裁下"); text: String(projectController.selectedClipData.cropBottom ?? 0) }
                        }
                        AppButton {
                            Layout.fillWidth: true; primary: true; text: qsTr("应用画面参数")
                            onClicked: projectController.setClipTransform(
                                projectController.selectedClipId,
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
                        Text { text: qsTr("片段音频"); color: Theme.text; font.pixelSize: 11; font.weight: Font.DemiBold }
                        InspectorField { id: gain; Layout.fillWidth: true; label: qsTr("增益 dB"); text: String(projectController.selectedClipData.gainDb ?? 0) }
                        InspectorField { id: pan; Layout.fillWidth: true; label: qsTr("声像"); text: String(projectController.selectedClipData.pan ?? 0) }
                        InspectorField { id: fadeIn; Layout.fillWidth: true; label: qsTr("淡入帧"); text: String(projectController.selectedClipData.fadeInFrames ?? 0) }
                        InspectorField { id: fadeOut; Layout.fillWidth: true; label: qsTr("淡出帧"); text: String(projectController.selectedClipData.fadeOutFrames ?? 0) }
                        AppButton {
                            Layout.fillWidth: true; primary: true; text: qsTr("应用音频参数")
                            onClicked: projectController.setClipAudio(
                                projectController.selectedClipId, Number(gain.text), Number(pan.text),
                                Number(fadeIn.text), Number(fadeOut.text))
                        }
                    }
                }

                RowLayout {
                    Layout.fillWidth: true
                    AppButton { Layout.fillWidth: true; text: qsTr("删除"); danger: true; onClicked: projectController.deleteClip(projectController.selectedClipId, false) }
                    AppButton { Layout.fillWidth: true; text: qsTr("波纹删除"); onClicked: projectController.deleteClip(projectController.selectedClipId, true) }
                }
            }

            EmptyState {
                Layout.fillWidth: true
                Layout.preferredHeight: 360
                visible: projectController.selectedAssetId.length === 0 && projectController.selectedClipId.length === 0
                iconText: "≡"
                title: qsTr("这里空空如也")
                description: qsTr("选择素材、片段、轨道、字幕或音频总线后，可在这里编辑属性。")
            }
        }
    }

}

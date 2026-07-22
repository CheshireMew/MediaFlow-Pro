import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "."
import "components"

ScrollView {
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

        Text {
            text: qsTr("片段属性")
            color: Theme.text
            font.pixelSize: Theme.fontSizeSection
            font.weight: Font.DemiBold
        }

        WebLayerPanel { playheadFrame: editScroll.playheadFrame }

        Panel {
            objectName: "editClipTimingPanel"
            Layout.fillWidth: true
            implicitHeight: timingContent.implicitHeight + 22
            visible: timelineController.selectedClipId.length > 0 && !webController.isWebClip
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
                    Switch {
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
            }
        }

        Panel {
            objectName: "editClipTransformPanel"
            Layout.fillWidth: true
            implicitHeight: transformContent.implicitHeight + 22
            visible: timelineController.selectedClipId.length > 0 && !webController.isWebClip
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
            text: qsTr("转场")
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
                AppButton {
                    required property var modelData
                    Layout.fillWidth: true
                    text: modelData.label
                    enabled: timelineController.selectedClipId.length > 0
                    onClicked: timelineController.addTransitionAfter(timelineController.selectedClipId, modelData.value, 15)
                }
            }
        }
        Panel {
            Layout.fillWidth: true
            implicitHeight: 176
            visible: timelineController.selectedTransitionId.length > 0
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
                    Layout.fillWidth: true
                    textRole: "label"
                    valueRole: "value"
                    model: root.transitionOptions
                    Component.onCompleted: currentIndex = Math.max(0, indexOfValue(timelineController.selectedTransitionData.kind || "dissolve"))
                }
                AppSpinBox {
                    id: transitionDuration
                    Layout.fillWidth: true
                    from: 1
                    to: 300
                    value: timelineController.selectedTransitionData.durationFrames || 15
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
            visible: timelineController.selectedClipId.length === 0 && timelineController.selectedTransitionId.length === 0
            iconText: "剪"
            title: qsTr("选择时间线片段")
            description: qsTr("选择后可以编辑画面、裁剪速度并添加转场。")
        }
    }
}

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "."
import "components"

ColumnLayout {
    id: root
    spacing: 10
    property var transitionOptions: timelineController.transitionOptions
    Text { text: qsTr("转场与效果"); color: Theme.text; font.pixelSize: Theme.fontSizeSection; font.weight: Font.DemiBold }
    Text {
        Layout.fillWidth: true
        text: qsTr("选择一个片段，在它与同轨道下一个相邻片段之间创建转场。")
        color: Theme.textMuted; font.pixelSize: Theme.fontSizeCaption; wrapMode: Text.WordWrap
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
                Component.onCompleted: currentIndex = Math.max(
                    0, indexOfValue(timelineController.selectedTransitionData.kind || "dissolve"))
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
                    onClicked: timelineController.updateTransition(
                        timelineController.selectedTransitionId,
                        transitionKind.currentValue,
                        transitionDuration.value)
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
    Panel {
        Layout.fillWidth: true
        implicitHeight: 112
        ColumnLayout {
            anchors.fill: parent; anchors.margins: 11; spacing: 5
            Text { text: qsTr("片段变换"); color: Theme.text; font.pixelSize: Theme.fontSizeCaption; font.weight: Font.DemiBold }
            Text { Layout.fillWidth: true; text: qsTr("位置、缩放、旋转、裁剪、透明度、速度和淡入淡出由右侧检查器编辑。所有修改进入同一个撤销栈。 "); color: Theme.textMuted; font.pixelSize: Theme.fontSizeCaption; wrapMode: Text.WordWrap }
        }
    }
    EmptyState {
        Layout.fillWidth: true
        Layout.fillHeight: true
        visible: timelineController.selectedClipId.length === 0
                 && timelineController.selectedTransitionId.length === 0
        iconText: "剪"
        title: qsTr("选择时间线片段")
        description: qsTr("选择后可以添加转场并编辑片段属性。")
    }
    Item { Layout.fillHeight: true }
}

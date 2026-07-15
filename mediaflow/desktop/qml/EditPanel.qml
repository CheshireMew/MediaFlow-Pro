import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "."
import "components"

ColumnLayout {
    id: root
    spacing: 10
    Text { text: qsTr("转场与效果"); color: Theme.text; font.pixelSize: 16; font.weight: Font.DemiBold }
    Text {
        Layout.fillWidth: true
        text: qsTr("选择一个片段，在它与同轨道下一个相邻片段之间创建转场。")
        color: Theme.textMuted; font.pixelSize: 11; wrapMode: Text.WordWrap
    }
    GridLayout {
        Layout.fillWidth: true
        columns: 2
        columnSpacing: 7
        rowSpacing: 7
        Repeater {
            model: [
                { label: qsTr("交叉溶解"), value: "dissolve" },
                { label: qsTr("淡化"), value: "fade" },
                { label: qsTr("淡黑"), value: "fade_black" },
                { label: qsTr("左擦除"), value: "wipe_left" },
                { label: qsTr("右擦除"), value: "wipe_right" },
                { label: qsTr("左滑动"), value: "slide_left" },
                { label: qsTr("右滑动"), value: "slide_right" },
                { label: qsTr("缩放"), value: "zoom" }
            ].filter(function(item) {
                return projectController.isTransitionAvailable(item.value)
            })
            AppButton {
                required property var modelData
                Layout.fillWidth: true
                text: modelData.label
                enabled: projectController.selectedClipId.length > 0
                onClicked: projectController.addTransitionAfter(projectController.selectedClipId, modelData.value, 15)
            }
        }
    }
    Panel {
        Layout.fillWidth: true
        implicitHeight: 176
        visible: projectController.selectedTransitionId.length > 0
        ColumnLayout {
            anchors.fill: parent
            anchors.margins: 10
            spacing: 7
            Text {
                text: qsTr("调整所选转场")
                color: Theme.text
                font.pixelSize: 11
                font.weight: Font.DemiBold
            }
            ComboBox {
                id: transitionKind
                Layout.fillWidth: true
                textRole: "label"
                valueRole: "value"
                model: [
                    { label: qsTr("交叉溶解"), value: "dissolve" },
                    { label: qsTr("淡化"), value: "fade" },
                    { label: qsTr("淡黑"), value: "fade_black" },
                    { label: qsTr("左擦除"), value: "wipe_left" },
                    { label: qsTr("右擦除"), value: "wipe_right" },
                    { label: qsTr("左滑动"), value: "slide_left" },
                    { label: qsTr("右滑动"), value: "slide_right" },
                    { label: qsTr("缩放"), value: "zoom" }
                ].filter(function(item) {
                    return projectController.isTransitionAvailable(item.value)
                })
                Component.onCompleted: currentIndex = Math.max(
                    0, indexOfValue(projectController.selectedTransitionData.kind || "dissolve"))
            }
            SpinBox {
                id: transitionDuration
                Layout.fillWidth: true
                from: 1
                to: 300
                value: projectController.selectedTransitionData.durationFrames || 15
                editable: true
            }
            RowLayout {
                Layout.fillWidth: true
                AppButton {
                    Layout.fillWidth: true
                    primary: true
                    text: qsTr("应用")
                    onClicked: projectController.updateTransition(
                        projectController.selectedTransitionId,
                        transitionKind.currentValue,
                        transitionDuration.value)
                }
                AppButton {
                    Layout.fillWidth: true
                    danger: true
                    text: qsTr("移除转场")
                    onClicked: projectController.removeTransition(projectController.selectedTransitionId)
                }
            }
        }
    }
    Panel {
        Layout.fillWidth: true
        implicitHeight: 112
        ColumnLayout {
            anchors.fill: parent; anchors.margins: 11; spacing: 5
            Text { text: qsTr("片段变换"); color: Theme.text; font.pixelSize: 11; font.weight: Font.DemiBold }
            Text { Layout.fillWidth: true; text: qsTr("位置、缩放、旋转、裁剪、透明度、速度和淡入淡出由右侧检查器编辑。所有修改进入同一个撤销栈。 "); color: Theme.textMuted; font.pixelSize: 10; wrapMode: Text.WordWrap }
        }
    }
    EmptyState {
        Layout.fillWidth: true
        Layout.fillHeight: true
        visible: projectController.selectedClipId.length === 0
                 && projectController.selectedTransitionId.length === 0
        iconText: "剪"
        title: qsTr("选择时间线片段")
        description: qsTr("选择后可以添加转场并编辑片段属性。")
    }
    Item { Layout.fillHeight: true }
}

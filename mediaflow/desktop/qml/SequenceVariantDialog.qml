import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "."
import "components"

AppDialog {
    id: root
    objectName: "sequenceVariantDialog"
    anchors.centerIn: parent
    implicitWidth: 480
    width: 480
    modal: true
    title: qsTr("从母版生成交付版本")
    standardButtons: Dialog.Save | Dialog.Cancel
    property var plan: mediaflow.workspaceSequenceController.variantPlan
    readonly property var planItems: plan.items || []
    readonly property int conflictCount: {
        let count = 0;
        for (const item of planItems) count += (item.conflicts || []).length;
        return count;
    }
    readonly property bool planReady: planItems.length > 0

    Component.onCompleted: standardButton(Dialog.Save).enabled = Qt.binding(
        function() { return root.planReady && (root.conflictCount === 0 || forceRegenerate.checked); })

    function selectedPresets() {
        const values = [];
        if (landscape.checked) values.push("landscape_16_9");
        if (portrait.checked) values.push("portrait_9_16");
        if (square.checked) values.push("square_1_1");
        if (social.checked) values.push("portrait_4_5");
        return values;
    }

    onOpened: {
        landscape.checked = false;
        portrait.checked = true;
        square.checked = true;
        social.checked = false;
        reframe.currentIndex = 0;
        forceRegenerate.checked = false;
        mediaflow.workspaceSequenceController.planDeliveryVariants(
            selectedPresets(), String(reframe.currentValue));
    }

    onAccepted: mediaflow.workspaceSequenceController.generateDeliveryVariants(
        selectedPresets(), String(reframe.currentValue), forceRegenerate.checked)

    contentItem: ColumnLayout {
        spacing: 10

        Text {
            Layout.fillWidth: true
            text: qsTr("每个版本都是可继续编辑的完整序列。母版变化后会同步到同一版本，人工调整会保留；双方改到同一处时先显示冲突。")
            color: Theme.textMuted
            font.pixelSize: Theme.fontSizeBodySmall
            wrapMode: Text.WordWrap
        }

        GridLayout {
            Layout.fillWidth: true
            columns: 2
            columnSpacing: 12
            rowSpacing: 4
            AppCheckBox {
                id: landscape
                objectName: "variantLandscapeCheck"
                text: qsTr("横屏 16:9 · 1920×1080")
            }
            AppCheckBox {
                id: portrait
                objectName: "variantPortraitCheck"
                text: qsTr("竖屏 9:16 · 1080×1920")
            }
            AppCheckBox {
                id: square
                objectName: "variantSquareCheck"
                text: qsTr("方形 1:1 · 1080×1080")
            }
            AppCheckBox {
                id: social
                objectName: "variantSocialCheck"
                text: qsTr("竖屏 4:5 · 1080×1350")
            }
        }

        Text {
            text: qsTr("画面适配")
            color: Theme.textMuted
            font.pixelSize: Theme.fontSizeCaption
        }
        AppComboBox {
            id: reframe
            objectName: "variantReframeMode"
            Layout.fillWidth: true
            textRole: "label"
            valueRole: "value"
            model: [
                { label: qsTr("居中铺满 · 自动裁掉多余边缘"), value: "center_fill" },
                { label: qsTr("完整适配 · 保留母版构图"), value: "fit" }
            ]
        }

        AppCheckBox {
            id: forceRegenerate
            objectName: "variantForceRegenerate"
            Layout.fillWidth: true
            text: qsTr("冲突时以母版为准（会覆盖冲突处的本地调整）")
        }

        AppButton {
            objectName: "variantPlanButton"
            Layout.alignment: Qt.AlignRight
            text: qsTr("检查变更")
            onClicked: mediaflow.workspaceSequenceController.planDeliveryVariants(
                root.selectedPresets(), String(reframe.currentValue))
        }

        Rectangle {
            Layout.fillWidth: true
            visible: root.planReady
            implicitHeight: planColumn.implicitHeight + 14
            radius: Theme.radiusSmall
            color: Theme.surfaceRaised
            border.color: root.conflictCount > 0 ? Theme.warning : Theme.borderSubtle
            ColumnLayout {
                id: planColumn
                anchors.fill: parent
                anchors.margins: 7
                Text {
                    Layout.fillWidth: true
                    text: root.conflictCount > 0
                        ? qsTr("发现 %1 个冲突，请保留本地调整，或勾选“以母版为准”后保存。").arg(root.conflictCount)
                        : qsTr("变更计划已就绪。")
                    color: root.conflictCount > 0 ? Theme.warning : Theme.textSubtle
                    font.pixelSize: Theme.fontSizeCaption
                    wrapMode: Text.WordWrap
                }
                Repeater {
                    model: root.planItems
                    delegate: Text {
                        required property var modelData
                        Layout.fillWidth: true
                        text: qsTr("%1 · %2 项变更 · %3 个冲突%4")
                            .arg(String(modelData.preset_id))
                            .arg((modelData.changes || []).length)
                            .arg((modelData.conflicts || []).length)
                            .arg((modelData.conflicts || []).length > 0
                                ? "\n" + (modelData.conflicts || []).map(item => String(item.path)).join("\n") : "")
                        color: Theme.textMuted
                        font.pixelSize: Theme.fontSizeCaption
                        font.family: Theme.monoFontFamily
                        wrapMode: Text.WrapAnywhere
                    }
                }
            }
        }
    }
}

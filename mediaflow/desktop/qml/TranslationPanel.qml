import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "."
import "components"

ColumnLayout {
    id: root
    spacing: 10

    function syncTargetLanguage() {
        const wanted = projectController.defaultTranslationLanguage
        for (var index = 0; index < targetLanguage.model.length; ++index) {
            if (targetLanguage.model[index].value === wanted) {
                targetLanguage.currentIndex = index
                return
            }
        }
        targetLanguage.currentIndex = 0
    }

    Component.onCompleted: syncTargetLanguage()
    Connections {
        target: projectController
        function onSettingsChanged() { root.syncTargetLanguage() }
    }
    Text { text: qsTr("翻译"); color: Theme.text; font.pixelSize: 16; font.weight: Font.DemiBold }
    Text {
        Layout.fillWidth: true
        text: qsTr("选择一个源字幕文档。译文会保留稳定的源分段关联，不会覆盖原文。")
        color: Theme.textMuted; font.pixelSize: 11; wrapMode: Text.WordWrap
    }
    ListView {
        id: translationDocuments
        Layout.fillWidth: true
        Layout.preferredHeight: 220
        clip: true
        spacing: 6
        model: projectController.subtitleDocumentsModel
        delegate: Rectangle {
            required property string documentId
            required property string language
            required property bool isSource
            required property int segmentCount
            width: translationDocuments.width
            height: 52
            radius: Theme.radiusSmall
            color: projectController.selectedDocumentId === documentId ? Theme.accentSoft : translationMouse.containsMouse ? Theme.surfaceHover : Theme.surfaceRaised
            border.color: projectController.selectedDocumentId === documentId ? Theme.accent : Theme.border
            RowLayout {
                anchors.fill: parent; anchors.margins: 9
                ColumnLayout {
                    Layout.fillWidth: true; spacing: 2
                    Text { text: language; color: Theme.text; font.pixelSize: 12; font.weight: Font.DemiBold }
                    Text { text: (isSource ? qsTr("源文档") : qsTr("译文")) + " · " + segmentCount + qsTr(" 条"); color: Theme.textMuted; font.pixelSize: 10 }
                }
            }
            MouseArea { id: translationMouse; anchors.fill: parent; hoverEnabled: true; onClicked: projectController.selectSubtitleDocument(documentId) }
        }
    }
    Text { text: qsTr("目标语言"); color: Theme.textMuted; font.pixelSize: 11 }
    ComboBox {
        id: targetLanguage
        Layout.fillWidth: true
        model: [
            { label: qsTr("选择目标语言"), value: "" },
            { label: qsTr("简体中文"), value: "zh_CN" },
            { label: qsTr("英语"), value: "en" },
            { label: qsTr("日语"), value: "ja" },
            { label: qsTr("繁体中文"), value: "zh_TW" },
            { label: qsTr("韩语"), value: "ko" },
            { label: qsTr("西班牙语"), value: "es" }
        ]
        textRole: "label"
        valueRole: "value"
    }
    AppButton {
        Layout.fillWidth: true
        primary: true
        text: qsTr("翻译所选文档")
        enabled: projectController.selectedDocumentId.length > 0
            && targetLanguage.currentValue.length > 0
        onClicked: projectController.translateDocument(projectController.selectedDocumentId, targetLanguage.currentValue)
    }
    Panel {
        Layout.fillWidth: true
        implicitHeight: 92
        ColumnLayout {
            anchors.fill: parent; anchors.margins: 11; spacing: 5
            Text { text: qsTr("执行规则"); color: Theme.text; font.pixelSize: 11; font.weight: Font.DemiBold }
            Text { Layout.fillWidth: true; text: qsTr("缺少 API 配置时任务会明确失败并保留原因，不会猜测翻译结果。"); color: Theme.textMuted; font.pixelSize: 10; wrapMode: Text.WordWrap }
        }
    }
    Item { Layout.fillHeight: true }
}

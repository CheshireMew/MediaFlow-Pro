import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "."
import "components"

ColumnLayout {
    spacing: 10
    RowLayout {
        Layout.fillWidth: true
        Text { text: qsTr("AI 高光"); color: Theme.text; font.pixelSize: 16; font.weight: Font.DemiBold }
        Item { Layout.fillWidth: true }
        AppButton {
            text: qsTr("分析")
            primary: true
            enabled: projectController.selectedDocumentId.length > 0
            onClicked: projectController.analyzeHighlights(projectController.selectedDocumentId)
        }
    }
    ComboBox {
        id: sourceDocument
        Layout.fillWidth: true
        model: projectController.subtitleDocumentsModel
        textRole: "language"
        valueRole: "documentId"
        onActivated: projectController.selectSubtitleDocument(currentValue)
        Component.onCompleted: if (count > 0) projectController.selectSubtitleDocument(currentValue)
    }
    Text {
        Layout.fillWidth: true
        text: qsTr("候选区间保存在项目中，可直接生成独立的 9:16 短视频序列。")
        color: Theme.textMuted; font.pixelSize: 11; wrapMode: Text.WordWrap
    }
    AppButton {
        Layout.fillWidth: true
        text: qsTr("批量创建全部短视频草稿")
        enabled: highlightList.count > 0
        onClicked: projectController.createAllHighlightShorts()
    }
    ListView {
        id: highlightList
        Layout.fillWidth: true
        Layout.fillHeight: true
        clip: true
        spacing: 8
        model: projectController.highlightsModel
        delegate: Rectangle {
            required property string highlightId
            required property int startFrame
            required property int endFrame
            required property string title
            required property string reason
            required property real score
            width: highlightList.width
            height: highlightBody.implicitHeight + 24
            radius: Theme.radius
            color: projectController.selectedHighlightId === highlightId ? Theme.accentSoft : highlightMouse.containsMouse ? Theme.surfaceHover : Theme.surfaceRaised
            border.color: projectController.selectedHighlightId === highlightId ? Theme.accent : Theme.border
            ColumnLayout {
                id: highlightBody
                anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
                anchors.margins: 11; spacing: 6
                RowLayout {
                    Layout.fillWidth: true
                    Text { Layout.fillWidth: true; text: title; color: Theme.text; font.pixelSize: 12; font.weight: Font.DemiBold; elide: Text.ElideRight }
                    Text { text: Math.round(score * 100) + "%"; color: Theme.accentHover; font.pixelSize: 10 }
                }
                Text { text: startFrame + " – " + endFrame; color: Theme.textMuted; font.pixelSize: 9; font.family: "Consolas" }
                Text { Layout.fillWidth: true; text: reason; color: Theme.textMuted; font.pixelSize: 10; wrapMode: Text.WordWrap }
                RowLayout {
                    Layout.fillWidth: true
                    AppButton { Layout.fillWidth: true; text: qsTr("预览"); onClicked: projectController.previewHighlight(highlightId) }
                    AppButton { Layout.fillWidth: true; text: qsTr("添加到主序列"); onClicked: projectController.addHighlightToMainSequence(highlightId) }
                }
                AppButton { Layout.fillWidth: true; primary: true; text: qsTr("创建短视频序列"); onClicked: projectController.createShortFromHighlight(highlightId) }
            }
            MouseArea { id: highlightMouse; anchors.fill: parent; hoverEnabled: true; acceptedButtons: Qt.LeftButton; propagateComposedEvents: true; onClicked: { projectController.selectHighlight(highlightId); mouse.accepted = false } }
        }
        EmptyState {
            anchors.fill: parent
            visible: highlightList.count === 0
            iconText: "AI"
            title: qsTr("还没有高光候选")
            description: qsTr("选择字幕文档并运行分析。候选结果会显示在这里。")
        }
    }
}

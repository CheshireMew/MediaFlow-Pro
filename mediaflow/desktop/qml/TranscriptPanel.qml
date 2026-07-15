import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "."
import "components"

ColumnLayout {
    spacing: 10

    RowLayout {
        Layout.fillWidth: true
        Text { text: qsTr("转录"); color: Theme.text; font.pixelSize: 16; font.weight: Font.DemiBold }
        Item { Layout.fillWidth: true }
        AppButton {
            text: qsTr("开始转录")
            primary: true
            enabled: projectController.selectedAssetId.length > 0
            onClicked: projectController.transcribeSelectedAsset()
        }
    }

    Text {
        Layout.fillWidth: true
        text: projectController.selectedAssetId.length > 0
              ? qsTr("当前素材已选中。模型、语言和设备使用项目的 ASR 设置。")
              : qsTr("请先到“媒体”模式选择一个视频或音频素材。")
        color: Theme.textMuted
        font.pixelSize: 11
        wrapMode: Text.WordWrap
    }

    Text { text: qsTr("字幕文档"); color: Theme.text; font.pixelSize: 12; font.weight: Font.DemiBold }
    ListView {
        id: documentList
        Layout.fillWidth: true
        Layout.preferredHeight: Math.min(180, contentHeight)
        clip: true
        spacing: 6
        model: projectController.subtitleDocumentsModel
        delegate: Rectangle {
            required property string documentId
            required property string language
            required property bool isSource
            required property int segmentCount
            width: documentList.width
            height: 50
            radius: Theme.radiusSmall
            color: projectController.selectedDocumentId === documentId ? Theme.accentSoft : docMouse.containsMouse ? Theme.surfaceHover : Theme.surfaceRaised
            border.color: projectController.selectedDocumentId === documentId ? Theme.accent : Theme.border
            RowLayout {
                anchors.fill: parent; anchors.margins: 9; spacing: 7
                Text { text: language; color: Theme.text; font.pixelSize: 12; font.weight: Font.DemiBold }
                Text { text: isSource ? qsTr("源字幕") : qsTr("翻译"); color: Theme.textMuted; font.pixelSize: 10 }
                Item { Layout.fillWidth: true }
                Text { text: segmentCount + qsTr(" 条"); color: Theme.textMuted; font.pixelSize: 10 }
            }
            MouseArea { id: docMouse; anchors.fill: parent; hoverEnabled: true; onClicked: projectController.selectSubtitleDocument(documentId) }
        }
    }

    RowLayout {
        Layout.fillWidth: true
        Text { text: qsTr("转录文本"); color: Theme.text; font.pixelSize: 12; font.weight: Font.DemiBold }
        Item { Layout.fillWidth: true }
        AppButton {
            text: qsTr("放入序列")
            enabled: projectController.selectedDocumentId.length > 0
            onClicked: projectController.placeSubtitleDocument(projectController.selectedDocumentId)
        }
    }
    ListView {
        id: segmentList
        Layout.fillWidth: true
        Layout.fillHeight: true
        clip: true
        spacing: 5
        model: projectController.subtitleSegmentsModel
        delegate: Rectangle {
            required property int startFrame
            required property int endFrame
            required property string text
            width: segmentList.width
            height: segmentText.implicitHeight + 27
            radius: Theme.radiusSmall
            color: Theme.surfaceRaised
            border.color: Theme.border
            ColumnLayout {
                anchors.fill: parent; anchors.margins: 8; spacing: 3
                Text { text: startFrame + " – " + endFrame; color: Theme.textMuted; font.pixelSize: 9; font.family: "Consolas" }
                Text { id: segmentText; Layout.fillWidth: true; text: parent.parent.text; color: Theme.text; font.pixelSize: 11; wrapMode: Text.WordWrap }
            }
        }
        EmptyState {
            anchors.fill: parent
            visible: segmentList.count === 0
            iconText: "字"
            title: qsTr("还没有转录结果")
            description: qsTr("选择媒体素材并开始转录，结果会直接保存到项目。")
        }
    }

    Text { text: qsTr("序列字幕"); color: Theme.text; font.pixelSize: 12; font.weight: Font.DemiBold }
    ListView {
        id: placementList
        Layout.fillWidth: true
        Layout.preferredHeight: Math.min(150, Math.max(72, contentHeight))
        clip: true
        spacing: 4
        model: projectController.subtitlePlacementsModel
        delegate: Rectangle {
            required property string placementId
            required property int startFrame
            required property int endFrame
            required property string text
            required property bool hasOverride
            width: placementList.width
            height: 46
            radius: Theme.radiusSmall
            color: projectController.selectedSubtitlePlacementId === placementId
                   ? Theme.accentSoft : placementMouse.containsMouse
                   ? Theme.surfaceHover : Theme.surfaceRaised
            border.color: hasOverride ? Theme.accent : Theme.border
            RowLayout {
                anchors.fill: parent
                anchors.margins: 7
                Text { text: startFrame + "–" + endFrame; color: Theme.textMuted; font.pixelSize: 9 }
                Text { Layout.fillWidth: true; text: parent.parent.text; color: Theme.text; elide: Text.ElideRight; font.pixelSize: 10 }
                Text { visible: hasOverride; text: qsTr("序列覆盖"); color: Theme.accentHover; font.pixelSize: 8 }
            }
            MouseArea {
                id: placementMouse
                anchors.fill: parent
                hoverEnabled: true
                onClicked: projectController.selectSubtitlePlacement(placementId)
            }
        }
    }
    Panel {
        Layout.fillWidth: true
        implicitHeight: 150
        visible: projectController.selectedSubtitlePlacementId.length > 0
        ColumnLayout {
            anchors.fill: parent
            anchors.margins: 8
            spacing: 6
            TextArea {
                id: placementText
                Layout.fillWidth: true
                Layout.fillHeight: true
                text: projectController.selectedSubtitlePlacementData.text || ""
                color: Theme.text
                wrapMode: TextEdit.Wrap
                background: Rectangle { color: Theme.window; border.color: Theme.border; radius: 4 }
            }
            RowLayout {
                Layout.fillWidth: true
                AppButton {
                    Layout.fillWidth: true
                    primary: true
                    text: qsTr("保存为序列覆盖")
                    onClicked: projectController.updateSubtitlePlacementText(
                        projectController.selectedSubtitlePlacementId, placementText.text, false)
                }
                AppButton {
                    Layout.fillWidth: true
                    text: qsTr("应用到源文档")
                    onClicked: projectController.updateSubtitlePlacementText(
                        projectController.selectedSubtitlePlacementId, placementText.text, true)
                }
            }
        }
    }
}

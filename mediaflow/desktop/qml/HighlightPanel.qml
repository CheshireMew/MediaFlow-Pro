import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Dialogs
import "."
import "components"

ColumnLayout {
    id: root
    spacing: 10

    FolderDialog {
        id: batchExportFolder
        title: qsTr("选择批量导出文件夹")
        onAccepted: highlightController.exportSelectedHighlights(selectedFolder.toString())
    }
    RowLayout {
        Layout.fillWidth: true
        Text { text: qsTr("AI 高光"); color: Theme.text; font.pixelSize: Theme.fontSizeSection; font.weight: Font.DemiBold }
        Item { Layout.fillWidth: true }
        AppButton {
            text: qsTr("分析")
            primary: true
            enabled: subtitleController.selectedDocumentId.length > 0
            onClicked: highlightController.analyzeHighlights(subtitleController.selectedDocumentId)
        }
    }
    AppComboBox {
        id: sourceDocument
        Layout.fillWidth: true
        model: subtitleController.subtitleDocumentsModel
        textRole: "language"
        valueRole: "documentId"
        onActivated: subtitleController.selectSubtitleDocument(currentValue)
        Component.onCompleted: if (count > 0) subtitleController.selectSubtitleDocument(currentValue)
    }
    Text {
        Layout.fillWidth: true
        text: qsTr("候选区间保存在项目中，可直接生成独立的 9:16 短视频序列。")
        color: Theme.textMuted; font.pixelSize: Theme.fontSizeCaption; wrapMode: Text.WordWrap
    }
    Panel {
        Layout.fillWidth: true
        implicitHeight: 116
        ColumnLayout {
            anchors.fill: parent
            anchors.margins: 8
            spacing: 6
            Text {
                text: qsTr("添加手动候选")
                color: Theme.text
                font.pixelSize: Theme.fontSizeBodySmall
                font.weight: Font.DemiBold
            }
            AppTextField {
                id: manualTitle
                Layout.fillWidth: true
                placeholderText: qsTr("片段标题（可选）")
            }
            RowLayout {
                Layout.fillWidth: true
                Text {
                    text: qsTr("开始帧")
                    color: Theme.textMuted
                    font.pixelSize: Theme.fontSizeCaption
                }
                AppSpinBox {
                    id: manualStart
                    Layout.fillWidth: true
                    from: 0
                    to: Math.max(1, Number(mediaController.selectedAssetData.durationFrames || 2147483647))
                    editable: true
                }
                Text {
                    text: qsTr("结束帧")
                    color: Theme.textMuted
                    font.pixelSize: Theme.fontSizeCaption
                }
                AppSpinBox {
                    id: manualEnd
                    Layout.fillWidth: true
                    from: 1
                    to: Math.max(1, Number(mediaController.selectedAssetData.durationFrames || 2147483647))
                    value: Math.max(1, Math.min(to, 450))
                    editable: true
                }
                AppButton {
                    text: qsTr("添加候选")
                    enabled: mediaController.selectedAssetId.length > 0 && manualEnd.value > manualStart.value
                    onClicked: highlightController.addManualHighlight(
                        manualStart.value, manualEnd.value, manualTitle.text)
                }
            }
        }
    }
    RowLayout {
        Layout.fillWidth: true
        AppButton {
            Layout.fillWidth: true
            text: qsTr("创建所选短视频草稿")
            enabled: highlightList.count > 0
            onClicked: highlightController.createAllHighlightShorts()
        }
        AppButton {
            Layout.fillWidth: true
            primary: true
            text: qsTr("快速导出所选")
            enabled: highlightList.count > 0
            onClicked: highlightController.exportSelectedHighlightsToDefaultLocation()
        }
        AppButton {
            text: qsTr("另存为…")
            enabled: highlightList.count > 0
            onClicked: batchExportFolder.open()
        }
    }
    Text {
        Layout.fillWidth: true
        text: qsTr("快速导出沿用当前序列已保存的编码、分辨率、字幕样式、水印和音频设置；每个候选片段单独输出。")
        color: Theme.textMuted
        font.pixelSize: Theme.fontSizeCaption
        wrapMode: Text.WordWrap
    }
    ListView {
        id: highlightList
        Layout.fillWidth: true
        Layout.fillHeight: true
        clip: true
        spacing: 8
        model: highlightController.highlightsModel
        delegate: Rectangle {
            required property string highlightId
            required property string sequenceId
            required property int startFrame
            required property int endFrame
            required property string title
            required property string reason
            required property real score
            required property bool selected
            width: highlightList.width
            height: highlightBody.implicitHeight + 24
            radius: Theme.radius
            opacity: selected ? 1.0 : 0.55
            color: highlightController.selectedHighlightId === highlightId ? Theme.accentSoft : highlightMouse.containsMouse ? Theme.surfaceHover : Theme.surfaceRaised
            border.color: highlightController.selectedHighlightId === highlightId ? Theme.accent : Theme.border
            ColumnLayout {
                id: highlightBody
                anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
                anchors.margins: 11; spacing: 6
                RowLayout {
                    Layout.fillWidth: true
                    Text { Layout.fillWidth: true; text: title; color: Theme.text; font.pixelSize: Theme.fontSizeBodySmall; font.weight: Font.DemiBold; elide: Text.ElideRight }
                    Text { text: Math.round(score * 100) + "%"; color: Theme.accentHover; font.pixelSize: Theme.fontSizeCaption }
                }
                Text { text: startFrame + " – " + endFrame; color: Theme.textMuted; font.pixelSize: Theme.fontSizeCaption; font.family: Theme.monoFontFamily }
                Text { Layout.fillWidth: true; text: reason; color: Theme.textMuted; font.pixelSize: Theme.fontSizeCaption; wrapMode: Text.WordWrap }
                RowLayout {
                    Layout.fillWidth: true
                    AppButton { Layout.fillWidth: true; text: qsTr("预览"); onClicked: highlightController.previewHighlight(highlightId) }
                    AppButton { Layout.fillWidth: true; text: qsTr("添加到主序列"); onClicked: highlightController.addHighlightToMainSequence(highlightId) }
                }
                RowLayout {
                    Layout.fillWidth: true
                    AppButton {
                        Layout.fillWidth: true
                        text: selected ? qsTr("已纳入导出") : qsTr("纳入导出")
                        onClicked: highlightController.setHighlightSelected(highlightId, !selected)
                    }
                    AppButton {
                        text: qsTr("删除")
                        onClicked: highlightController.deleteHighlight(highlightId)
                    }
                }
                AppButton {
                    Layout.fillWidth: true
                    primary: true
                    text: sequenceId.length > 0 ? qsTr("打开短视频序列") : qsTr("创建短视频序列")
                    onClicked: highlightController.createShortFromHighlight(highlightId)
                }
            }
            MouseArea { id: highlightMouse; anchors.fill: parent; hoverEnabled: true; acceptedButtons: Qt.LeftButton; propagateComposedEvents: true; onClicked: { highlightController.selectHighlight(highlightId); mouse.accepted = false } }
        }
        EmptyState {
            anchors.fill: parent
            visible: highlightList.count === 0
            iconText: "AI"
            title: qsTr("还没有高光候选")
            description: qsTr("选择字幕文档并运行分析。候选结果会显示在这里。")
        }
    }
    Panel {
        Layout.fillWidth: true
        implicitHeight: 132
        visible: highlightController.selectedHighlightId.length > 0
        ColumnLayout {
            anchors.fill: parent
            anchors.margins: 8
            spacing: 6
            Text {
                text: qsTr("编辑候选")
                color: Theme.text
                font.pixelSize: Theme.fontSizeBodySmall
                font.weight: Font.DemiBold
            }
            AppTextField {
                id: editTitle
                Layout.fillWidth: true
                text: highlightController.selectedHighlightData.title || ""
            }
            RowLayout {
                Layout.fillWidth: true
                Text {
                    text: qsTr("开始帧")
                    color: Theme.textMuted
                    font.pixelSize: Theme.fontSizeCaption
                }
                AppSpinBox {
                    id: editStart
                    Layout.fillWidth: true
                    from: 0
                    to: 2147483647
                    value: Number(highlightController.selectedHighlightData.startFrame || 0)
                    editable: true
                }
                Text {
                    text: qsTr("结束帧")
                    color: Theme.textMuted
                    font.pixelSize: Theme.fontSizeCaption
                }
                AppSpinBox {
                    id: editEnd
                    Layout.fillWidth: true
                    from: 1
                    to: 2147483647
                    value: Number(highlightController.selectedHighlightData.endFrame || 1)
                    editable: true
                }
                AppButton {
                    text: qsTr("保存候选")
                    primary: true
                    onClicked: highlightController.updateHighlight(
                        highlightController.selectedHighlightId,
                        editStart.value,
                        editEnd.value,
                        editTitle.text)
                }
            }
        }
    }
}

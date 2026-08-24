import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Dialogs
import ".."

ColumnLayout {
    id: editor
    spacing: 7
    property int playheadFrame: 0
    readonly property bool canEdit: Boolean(mediaflow.workspaceViewController.actionCapabilities.canEdit)
    signal seekRequested(int frame)

    property alias showSearch: searchPanel.expanded

    FileDialog {
        id: exportSubtitleDialog
        title: qsTr("导出字幕文档")
        fileMode: FileDialog.SaveFile
        nameFilters: [qsTr("SRT 字幕 (*.srt)")]
        onAccepted: mediaflow.subtitleEditingController.exportSubtitleDocument(mediaflow.subtitleViewController.selectedDocumentId, selectedFile.toString())
    }

    RowLayout {
        Layout.fillWidth: true
        AppButton {
            text: qsTr("添加")
            enabled: editor.canEdit && mediaflow.subtitleViewController.selectedDocumentId.length > 0
            onClicked: mediaflow.subtitleEditingController.addSubtitleSegment()
        }
        AppButton {
            text: qsTr("合并")
            enabled: editor.canEdit && mediaflow.subtitleViewController.selectedSubtitleSegmentIds.length >= 2
            onClicked: mediaflow.subtitleEditingController.mergeSelectedSubtitleSegments()
        }
        AppButton {
            text: qsTr("删除")
            enabled: editor.canEdit && mediaflow.subtitleViewController.selectedSubtitleSegmentIds.length > 0
            onClicked: mediaflow.subtitleEditingController.deleteSelectedSubtitleSegments()
        }
        Item {
            Layout.fillWidth: true
        }
        AppMenuButton {
            id: subtitleMoreButton
            text: qsTr("更多")
            onClicked: subtitleMoreMenu.open()
            AppMenu {
                id: subtitleMoreMenu
                y: subtitleMoreButton.height + 4
                AppMenuItem {
                    text: editor.showSearch ? qsTr("关闭查找替换") : qsTr("查找替换")
                    onTriggered: editor.showSearch = !editor.showSearch
                }
                AppMenuItem {
                    text: qsTr("导出 SRT")
                    enabled: mediaflow.subtitleViewController.selectedDocumentId.length > 0
                    onTriggered: exportSubtitleDialog.open()
                }
                AppMenuSeparator {}
                AppMenuItem {
                    text: qsTr("翻译所选")
                    enabled: editor.canEdit && mediaflow.subtitleViewController.selectedSubtitleSegmentIds.length > 0
                    onTriggered: mediaflow.subtitleTranslationController.translateSelectedSubtitleSegments()
                }
                AppMenuItem {
                    text: qsTr("复制 SRT")
                    enabled: editor.canEdit && mediaflow.subtitleViewController.selectedSubtitleSegmentIds.length > 0
                    onTriggered: mediaflow.subtitleEditingController.copySelectedSubtitleSegments()
                }
                AppMenuItem {
                    text: qsTr("粘贴替换")
                    enabled: editor.canEdit && mediaflow.subtitleViewController.selectedSubtitleSegmentIds.length > 0
                    onTriggered: mediaflow.subtitleEditingController.pasteReplaceSelectedSubtitleSegments()
                }
                AppMenuItem {
                    text: qsTr("打开文件夹")
                    enabled: mediaflow.subtitleViewController.selectedDocumentId.length > 0
                    onTriggered: mediaflow.subtitleEditingController.openSubtitleFolder()
                }
            }
        }
    }

    SubtitleSearchPanel {
        id: searchPanel
        canEdit: editor.canEdit
    }

    RowLayout {
        Layout.fillWidth: true
        Text {
            text: qsTr("字幕段")
            color: Theme.text
            font.pixelSize: Theme.fontSizeBodySmall
            font.weight: Font.DemiBold
        }
        Text {
            text: mediaflow.subtitleViewController.selectedSubtitleSegmentIds.length > 1 ? qsTr("已选 %1 条").arg(mediaflow.subtitleViewController.selectedSubtitleSegmentIds.length) : ""
            color: Theme.accentHover
            font.pixelSize: Theme.fontSizeCaption
        }
        Item {
            Layout.fillWidth: true
        }
        Text {
            text: qsTr("长度阈值")
            color: Theme.textMuted
            font.pixelSize: Theme.fontSizeCaption
        }
        AppSpinBox {
            id: smartSplitLimit
            Layout.preferredWidth: 90
            from: 1
            to: 200
            value: 24
            editable: true
        }
        AppButton {
            text: qsTr("智能拆分")
            enabled: editor.canEdit && mediaflow.subtitleViewController.selectedDocumentId.length > 0
            onClicked: mediaflow.subtitleEditingController.smartSplitSubtitleDocument(smartSplitLimit.value)
        }
        AppButton {
            text: qsTr("修复重叠")
            enabled: editor.canEdit && mediaflow.subtitleViewController.selectedDocumentId.length > 0
            onClicked: mediaflow.subtitleEditingController.fixSubtitleOverlaps()
        }
    }

    SubtitleSegmentList {
        canEdit: editor.canEdit
        onSeekRequested: function (frame) {
            editor.seekRequested(frame);
        }
    }

    SubtitleSegmentEditor {
        playheadFrame: editor.playheadFrame
        canEdit: editor.canEdit
    }
}

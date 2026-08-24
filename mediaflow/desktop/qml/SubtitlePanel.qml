import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Dialogs
import "."
import "components"

AppScrollView {
    id: subtitleScroll
    objectName: "subtitlePanel"
    clip: true
    contentWidth: availableWidth
    property int playheadFrame: 0
    property bool playbackActive: false
    signal seekRequested(int frame)
    signal modeRequested(string mode)
    signal importRequested

    onPlayheadFrameChanged: {
        if (playbackActive)
            mediaflow.subtitleViewController.followSubtitleAtFrame(playheadFrame);
    }

    ColumnLayout {
        id: root
        width: subtitleScroll.availableWidth
        spacing: 9

        Text {
            text: qsTr("字幕文档")
            color: Theme.text
            font.pixelSize: Theme.fontSizeBodySmall
            font.weight: Font.DemiBold
        }
        ListView {
            id: documentList
            Layout.fillWidth: true
            Layout.preferredHeight: Math.min(116, Math.max(52, contentHeight))
            visible: count > 0
            clip: true
            spacing: 5
            model: mediaflow.subtitleViewController.subtitleDocumentsModel
            delegate: Rectangle {
                required property string documentId
                required property string language
                required property bool isSource
                required property int segmentCount
                width: documentList.width
                height: 48
                radius: Theme.radiusSmall
                color: mediaflow.subtitleViewController.selectedDocumentId === documentId ? Theme.accentSoft : docMouse.containsMouse ? Theme.surfaceHover : Theme.surfaceRaised
                border.color: mediaflow.subtitleViewController.selectedDocumentId === documentId ? Theme.accent : Theme.border
                RowLayout {
                    anchors.fill: parent
                    anchors.margins: 8
                    spacing: 7
                    Text {
                        text: language
                        color: Theme.text
                        font.pixelSize: Theme.fontSizeBodySmall
                        font.weight: Font.DemiBold
                    }
                    Text {
                        text: isSource ? qsTr("源字幕") : qsTr("翻译")
                        color: Theme.textMuted
                        font.pixelSize: Theme.fontSizeCaption
                    }
                    Item {
                        Layout.fillWidth: true
                    }
                    Text {
                        text: segmentCount + qsTr(" 条")
                        color: Theme.textMuted
                        font.pixelSize: Theme.fontSizeCaption
                    }
                }

                MouseArea {
                    id: docMouse
                    anchors.fill: parent
                    hoverEnabled: true
                    onClicked: mediaflow.subtitleViewController.selectSubtitleDocument(documentId)
                }
            }
        }

        ColumnLayout {
            Layout.fillWidth: true
            visible: documentList.count === 0
            spacing: 8
            EmptyState {
                Layout.fillWidth: true
                Layout.preferredHeight: 120
                iconName: "subtitle"
                title: qsTr("还没有字幕文档")
                description: qsTr("识别当前时间轴的声音，或导入已有的 SRT、WebVTT、ASS 字幕。")
            }
            AppButton {
                objectName: "subtitleStartTranscriptionButton"
                Layout.fillWidth: true
                primary: true
                text: qsTr("识别时间轴声音")
                enabled: Boolean(mediaflow.workspaceViewController.actionCapabilities.canStartTasks)
                onClicked: subtitleScroll.modeRequested("transcript")
            }
            AppButton {
                objectName: "subtitleImportFileButton"
                Layout.fillWidth: true
                text: qsTr("导入字幕文件")
                enabled: Boolean(mediaflow.workspaceViewController.actionCapabilities.canImport)
                onClicked: subtitleScroll.importRequested()
            }
        }

        AppTabBar {
            id: subtitleTabs
            Layout.fillWidth: true
            visible: documentList.count > 0
            AppTabButton {
                text: qsTr("文档编辑")
            }
            AppTabButton {
                text: qsTr("序列字幕")
            }
        }

        StackLayout {
            Layout.fillWidth: true
            Layout.preferredHeight: currentIndex === 0 ? documentEditor.implicitHeight : sequenceEditor.implicitHeight
            currentIndex: subtitleTabs.currentIndex
            visible: documentList.count > 0

            SubtitleDocumentEditor {
                id: documentEditor
                playheadFrame: subtitleScroll.playheadFrame
                onSeekRequested: function (frame) {
                    subtitleScroll.seekRequested(frame);
                }
            }

            SubtitleSequenceEditor {
                id: sequenceEditor
            }
        }
    }
}

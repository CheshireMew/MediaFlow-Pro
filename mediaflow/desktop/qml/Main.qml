import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "."
import "components"

ApplicationWindow {
    id: window
    width: Math.max(minimumWidth, projectController.settingsData.windowWidth || 1600)
    height: Math.max(minimumHeight, projectController.settingsData.windowHeight || 980)
    minimumWidth: 1180
    minimumHeight: 720
    visible: true
    title: projectController.hasProject ? projectController.projectName + " — MediaFlow Pro" : "MediaFlow Pro"
    color: Theme.window
    palette.window: Theme.window
    palette.windowText: Theme.text
    palette.base: Theme.surfaceRaised
    palette.alternateBase: Theme.surface
    palette.text: Theme.text
    palette.button: Theme.surfaceRaised
    palette.buttonText: Theme.text
    palette.highlight: Theme.accent
    palette.highlightedText: "white"
    palette.placeholderText: Theme.textMuted
    palette.toolTipBase: Theme.surfaceRaised
    palette.toolTipText: Theme.text
    onClosing: projectController.saveWindowSize(width, height)

    Loader {
        id: pageLoader
        objectName: "pageLoader"
        anchors.fill: parent
        sourceComponent: projectController.hasProject ? workspaceComponent : homeComponent
    }
    Component { id: homeComponent; HomeView {} }
    Component { id: workspaceComponent; Workspace {} }

    Dialog {
        id: downloadAnalysisDialog
        anchors.centerIn: parent
        width: 480
        modal: true
        title: qsTr("确认下载")
        standardButtons: Dialog.NoButton
        closePolicy: Popup.NoAutoClose
        contentItem: ColumnLayout {
            spacing: 10
            Text {
                Layout.fillWidth: true
                text: projectController.downloadAnalysisData.title || qsTr("已完成链接分析")
                color: Theme.text
                font.pixelSize: 14
                font.weight: Font.DemiBold
                wrapMode: Text.WordWrap
            }
            Text {
                Layout.fillWidth: true
                text: projectController.downloadAnalysisData.is_playlist
                      ? qsTr("播放列表 · %1 项 · %2").arg(
                            projectController.downloadAnalysisData.entry_count).arg(
                            projectController.downloadAnalysisData.extractor || "yt-dlp")
                      : qsTr("单个视频 · %1").arg(
                            projectController.downloadAnalysisData.extractor || "yt-dlp")
                color: Theme.textMuted
                font.pixelSize: 10
            }
            ComboBox {
                id: downloadResolution
                Layout.fillWidth: true
                textRole: "label"; valueRole: "value"
                model: [
                    {label: qsTr("最佳可用质量"), value: "best"},
                    {label: "2160p", value: "2160p"},
                    {label: "1440p", value: "1440p"},
                    {label: "1080p", value: "1080p"},
                    {label: "720p", value: "720p"},
                    {label: "480p", value: "480p"}
                ]
            }
            TextField {
                id: playlistItems
                Layout.fillWidth: true
                visible: projectController.downloadAnalysisData.is_playlist || false
                placeholderText: qsTr("播放列表项目，例如 1-5,8（留空为全部）")
                color: Theme.text
            }
            RowLayout {
                Layout.fillWidth: true
                AppButton {
                    Layout.fillWidth: true
                    text: qsTr("取消")
                    onClicked: projectController.dismissDownloadAnalysis()
                }
                AppButton {
                    Layout.fillWidth: true
                    primary: true
                    text: qsTr("开始下载")
                    onClicked: projectController.startAnalyzedDownload(
                        String(downloadResolution.currentValue), playlistItems.text)
                }
            }
        }
    }

    Popup {
        id: errorPopup
        x: (window.width - width) / 2
        y: 22
        width: Math.min(560, window.width - 48)
        height: errorText.implicitHeight + 28
        modal: false
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
        background: Rectangle {
            color: "#402127"
            border.color: Theme.danger
            radius: Theme.radius
        }
        contentItem: Text {
            id: errorText
            color: "#ffd8dc"
            wrapMode: Text.WordWrap
            font.pixelSize: 12
            verticalAlignment: Text.AlignVCenter
        }
        Timer { id: errorTimer; interval: 5000; onTriggered: errorPopup.close() }
    }

    Connections {
        target: projectController
        function onErrorOccurred(message) {
            errorText.text = message
            errorPopup.open()
            errorTimer.restart()
        }
        function onDownloadAnalysisChanged() {
            if (projectController.downloadAnalysisReady)
                downloadAnalysisDialog.open()
            else
                downloadAnalysisDialog.close()
        }
    }
}

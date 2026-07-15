import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Dialogs
import "."
import "components"

ColumnLayout {
    spacing: 10
    FileDialog {
        id: importDialog
        title: qsTr("导入媒体")
        fileMode: FileDialog.OpenFile
        currentFolder: projectController.defaultImportDirectoryUrl
        nameFilters: [qsTr("媒体文件 (*.mp4 *.mov *.mkv *.webm *.mp3 *.wav *.flac *.png *.jpg *.jpeg)"), qsTr("所有文件 (*)")]
        onAccepted: projectController.importMedia(selectedFile.toString())
    }
    FolderDialog {
        id: batchRelinkDialog
        title: qsTr("选择离线素材所在目录")
        onAccepted: projectController.relinkOfflineMedia(selectedFolder.toString())
    }

    RowLayout {
        Layout.fillWidth: true
        Text {
            text: qsTr("媒体")
            color: Theme.text
            font.pixelSize: 16
            font.weight: Font.DemiBold
        }
        Item { Layout.fillWidth: true }
        AppButton {
            visible: projectController.offlineAssetCount > 0
            text: qsTr("批量重新定位 (%1)").arg(projectController.offlineAssetCount)
            onClicked: batchRelinkDialog.open()
        }
        AppButton { text: qsTr("导入"); primary: true; onClicked: importDialog.open() }
    }

    TextField {
        id: search
        Layout.fillWidth: true
        implicitHeight: 34
        placeholderText: qsTr("搜索素材")
        color: Theme.text
        placeholderTextColor: Theme.textMuted
        leftPadding: 12
        background: Rectangle {
            radius: Theme.radiusSmall
            color: Theme.surfaceRaised
            border.color: search.activeFocus ? Theme.accent : Theme.border
        }
    }

    ListView {
        id: assetList
        Layout.fillWidth: true
        Layout.fillHeight: true
        clip: true
        spacing: 8
        model: projectController.assetsModel

        delegate: Rectangle {
            required property string assetId
            required property string name
            required property string kind
            required property string status
            required property bool proxyReady
            required property bool waveformReady
            width: assetList.width
            height: visible ? 76 : 0
            visible: search.text.length === 0 || name.toLowerCase().includes(search.text.toLowerCase())
            radius: Theme.radiusSmall
            color: projectController.selectedAssetId === assetId ? Theme.accentSoft : assetMouse.containsMouse ? Theme.surfaceHover : Theme.surfaceRaised
            border.color: projectController.selectedAssetId === assetId ? Theme.accent : Theme.border

            RowLayout {
                anchors.fill: parent
                anchors.margins: 9
                spacing: 10
                Rectangle {
                    width: 54
                    height: 54
                    radius: 7
                    color: kind === "audio" ? "#382d54" : kind === "image" ? "#493b27" : "#173754"
                    Text {
                        anchors.centerIn: parent
                        text: kind === "audio" ? "♫" : kind === "image" ? "▧" : "▶"
                        color: Theme.text
                        font.pixelSize: 18
                    }
                }
                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 5
                    Text {
                        Layout.fillWidth: true
                        text: name
                        color: Theme.text
                        font.pixelSize: 12
                        font.weight: Font.Medium
                        elide: Text.ElideRight
                    }
                    RowLayout {
                        spacing: 6
                        Text { text: status === "online" ? qsTr("可用") : qsTr("离线"); color: status === "online" ? Theme.success : Theme.danger; font.pixelSize: 10 }
                        Text { text: proxyReady ? qsTr("代理") : ""; color: Theme.textMuted; font.pixelSize: 10 }
                        Text { text: waveformReady ? qsTr("波形") : ""; color: Theme.textMuted; font.pixelSize: 10 }
                    }
                }
                AppButton {
                    text: "+"
                    width: 34
                    enabled: status === "online"
                    onClicked: projectController.addAssetToTimeline(assetId)
                }
            }
            MouseArea {
                id: assetMouse
                anchors.fill: parent
                anchors.rightMargin: 42
                hoverEnabled: true
                onClicked: projectController.selectAsset(assetId)
                onDoubleClicked: projectController.addAssetToTimeline(assetId)
            }
        }

        EmptyState {
            anchors.fill: parent
            visible: assetList.count === 0
            iconText: "＋"
            title: qsTr("导入第一个素材")
            description: qsTr("支持视频、音频和图片。下载的视频也会自动出现在这里。")
        }
    }
}

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Dialogs
import "."
import "components"

Item {
    id: root
    ColumnLayout {
        anchors.fill: parent
        spacing: 10
        FileDialog {
            id: importDialog
            title: qsTr("导入媒体")
            fileMode: FileDialog.OpenFile
            currentFolder: workspaceController.defaultImportDirectoryUrl
            nameFilters: [qsTr("媒体文件 (*.mp4 *.mov *.mkv *.webm *.mp3 *.wav *.flac *.png *.jpg *.jpeg *.srt *.vtt *.ass *.ssa)"), qsTr("所有文件 (*)")]
            onAccepted: mediaController.importMedia(selectedFile.toString())
        }
        FolderDialog {
            id: batchRelinkDialog
            title: qsTr("选择离线素材所在目录")
            onAccepted: mediaController.relinkOfflineMedia(selectedFolder.toString())
        }

        RowLayout {
            Layout.fillWidth: true
            Text {
                text: qsTr("媒体")
                color: Theme.text
                font.pixelSize: Theme.fontSizeSection
                font.weight: Font.DemiBold
            }
            Item {
                Layout.fillWidth: true
            }
            AppButton {
                visible: mediaController.selectedAssetIds.length > 1
                text: qsTr("添加所选 (%1)").arg(mediaController.selectedAssetIds.length)
                onClicked: mediaController.addSelectedAssetsToTimeline()
            }
            AppButton {
                visible: workspaceController.offlineAssetCount > 0
                text: qsTr("批量重新定位 (%1)").arg(workspaceController.offlineAssetCount)
                onClicked: batchRelinkDialog.open()
            }
            AppButton {
                text: qsTr("导入")
                primary: true
                onClicked: importDialog.open()
            }
        }

        AppTextField {
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
            model: mediaController.assetsModel

            delegate: Rectangle {
                id: assetDelegate
                objectName: "mediaAssetDelegate"
                required property string assetId
                required property string name
                required property string kind
                required property string status
                required property bool proxyReady
                required property bool waveformReady
                property var draggedAssetIds: mediaController.isAssetSelected(assetId) ? mediaController.selectedAssetIds : [assetId]
                width: assetList.width
                height: visible ? 76 : 0
                visible: search.text.length === 0 || name.toLowerCase().includes(search.text.toLowerCase())
                radius: Theme.radiusSmall
                color: mediaController.isAssetSelected(assetId) ? Theme.accentSoft : assetMouse.containsMouse ? Theme.surfaceHover : Theme.surfaceRaised
                border.color: mediaController.isAssetSelected(assetId) ? Theme.accent : Theme.border

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
                            font.pixelSize: Theme.fontSizeTitle
                        }
                    }
                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 5
                        Text {
                            Layout.fillWidth: true
                            text: name
                            color: Theme.text
                            font.pixelSize: Theme.fontSizeBodySmall
                            font.weight: Font.Medium
                            elide: Text.ElideRight
                        }
                        RowLayout {
                            spacing: 6
                            Text {
                                text: status === "online" ? qsTr("可用") : qsTr("离线")
                                color: status === "online" ? Theme.success : Theme.danger
                                font.pixelSize: Theme.fontSizeCaption
                            }
                            Text {
                                text: proxyReady ? qsTr("代理") : ""
                                color: Theme.textMuted
                                font.pixelSize: Theme.fontSizeCaption
                            }
                            Text {
                                text: waveformReady ? qsTr("波形") : ""
                                color: Theme.textMuted
                                font.pixelSize: Theme.fontSizeCaption
                            }
                        }
                    }
                    AppButton {
                        text: "+"
                        width: 34
                        enabled: status === "online"
                        onClicked: mediaController.addAssetToTimeline(assetId)
                    }
                }
                MouseArea {
                    id: assetMouse
                    anchors.fill: parent
                    anchors.rightMargin: 42
                    hoverEnabled: true
                    drag.target: assetDragProxy
                    drag.axis: Drag.XAndYAxis
                    onClicked: function (mouse) {
                        mediaController.selectAsset(assetId, (mouse.modifiers & Qt.ControlModifier) !== 0);
                    }
                    onDoubleClicked: mediaController.addAssetToTimeline(assetId)
                    onReleased: {
                        assetDragProxy.Drag.drop();
                        assetDragProxy.x = 0;
                        assetDragProxy.y = 0;
                    }
                    onCanceled: {
                        assetDragProxy.x = 0;
                        assetDragProxy.y = 0;
                    }
                }
                Rectangle {
                    id: assetDragProxy
                    width: assetDelegate.width
                    height: assetDelegate.height
                    radius: Theme.radiusSmall
                    color: Theme.accentSoft
                    border.color: Theme.accent
                    opacity: Drag.active ? 0.88 : 0
                    visible: Drag.active
                    z: 100
                    Drag.active: assetMouse.drag.active && assetDelegate.status === "online"
                    Drag.source: assetDelegate
                    Drag.keys: ["mediaflowAsset"]
                    Drag.hotSpot.x: width / 2
                    Drag.hotSpot.y: height / 2
                    Text {
                        anchors.fill: parent
                        anchors.margins: 10
                        text: assetDelegate.draggedAssetIds.length > 1 ? qsTr("%1 个素材").arg(assetDelegate.draggedAssetIds.length) : assetDelegate.name
                        color: Theme.text
                        elide: Text.ElideRight
                        verticalAlignment: Text.AlignVCenter
                    }
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

    DropArea {
        id: fileDropArea
        objectName: "mediaFileDropArea"
        anchors.fill: parent
        z: 200
        onDropped: function (drop) {
            if (!drop.hasUrls)
                return;
            mediaController.importFiles(drop.urls);
            drop.acceptProposedAction();
        }
    }

    Rectangle {
        anchors.fill: parent
        visible: fileDropArea.containsDrag
        z: 201
        color: "#5c132a41"
        border.width: 2
        border.color: Theme.accent
        radius: Theme.radiusSmall
        Text {
            anchors.centerIn: parent
            text: qsTr("释放以导入素材")
            color: Theme.text
            font.pixelSize: Theme.fontSizeBodyLarge
            font.weight: Font.DemiBold
        }
    }
}

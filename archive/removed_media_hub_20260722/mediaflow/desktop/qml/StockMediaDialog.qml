import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "."
import "components"

Dialog {
    id: root
    objectName: "stockMediaDialog"
    anchors.centerIn: parent
    width: 760
    height: 620
    modal: true
    title: qsTr("在线素材库")
    standardButtons: Dialog.Close
    contentItem: ColumnLayout {
        spacing: 9
        RowLayout {
            Layout.fillWidth: true
            AppComboBox {
                id: provider
                objectName: "stockProviderCombo"
                Layout.preferredWidth: 150
                textRole: "text"
                valueRole: "value"
                model: [
                    {text: "Pexels · " + qsTr("视频"), value: "pexels"},
                    {text: "Pixabay · " + qsTr("视频"), value: "pixabay"},
                    {text: "Unsplash · " + qsTr("图片"), value: "unsplash"}
                ]
            }
            AppTextField {
                id: query
                objectName: "stockSearchField"
                Layout.fillWidth: true
                placeholderText: qsTr("搜索人物、城市、自然、商业等")
                onAccepted: searchButton.clicked()
            }
            AppButton {
                id: searchButton
                objectName: "stockSearchButton"
                text: qsTr("搜索")
                primary: true
                enabled: query.text.trim().length > 0 && !mediaController.stockMediaSearchBusy
                onClicked: mediaController.searchStockMedia(provider.currentValue, query.text)
            }
        }
        Text {
            Layout.fillWidth: true
            text: qsTr("结果来自所选平台；导入时会保存来源和作者信息。API Key 在设置的下载页面填写。")
            color: Theme.textMuted
            font.pixelSize: Theme.fontSizeCaption
            wrapMode: Text.WordWrap
        }
        BusyIndicator {
            Layout.alignment: Qt.AlignHCenter
            running: mediaController.stockMediaSearchBusy
            visible: running
        }
        ListView {
            id: results
            objectName: "stockMediaResultsView"
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            spacing: 7
            model: mediaController.stockMediaResults
            ScrollBar.vertical: ScrollBar { }
            delegate: Rectangle {
                required property var modelData
                width: results.width
                height: 116
                radius: Theme.radiusSmall
                color: Theme.surfaceRaised
                border.color: Theme.border
                RowLayout {
                    anchors.fill: parent
                    anchors.margins: 8
                    spacing: 10
                    Image {
                        Layout.preferredWidth: 160
                        Layout.fillHeight: true
                        source: modelData.preview_url
                        fillMode: Image.PreserveAspectCrop
                        asynchronous: true
                        sourceSize.width: 320
                    }
                    ColumnLayout {
                        Layout.fillWidth: true
                        Text {
                            Layout.fillWidth: true
                            text: modelData.title
                            color: Theme.text
                            font.weight: Font.DemiBold
                            elide: Text.ElideRight
                        }
                        Text {
                            Layout.fillWidth: true
                            text: qsTr("%1 × %2 · 作者 %3")
                                .arg(modelData.width).arg(modelData.height).arg(modelData.attribution)
                            color: Theme.textMuted
                            font.pixelSize: Theme.fontSizeCaption
                            elide: Text.ElideRight
                        }
                        Text {
                            Layout.fillWidth: true
                            text: modelData.source_url
                            color: Theme.accent
                            font.pixelSize: 10
                            elide: Text.ElideMiddle
                        }
                    }
                    AppButton {
                        objectName: "importStockMediaButton_" + modelData.id.replace(/[^a-zA-Z0-9]/g, "_")
                        text: qsTr("导入")
                        primary: true
                        onClicked: mediaController.importStockMedia(modelData.id)
                    }
                }
            }
        }
        EmptyState {
            Layout.fillWidth: true
            Layout.fillHeight: true
            visible: !mediaController.stockMediaSearchBusy && results.count === 0
            title: qsTr("搜索可直接使用的在线素材")
            description: qsTr("Pexels 和 Pixabay 提供视频，Unsplash 提供图片。")
        }
    }
}

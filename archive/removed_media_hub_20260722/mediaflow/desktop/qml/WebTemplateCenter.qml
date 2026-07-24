import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtWebEngine
import "."
import "components"

Dialog {
    id: root
    objectName: "webTemplateCenter"
    property int playheadFrame: 0
    property int selectedIndex: 0
    readonly property var records: {
        const needle = search.text.trim().toLowerCase();
        const selectedCategory = String(category.currentValue || "");
        return (webController.componentOptions || []).filter(function(record) {
            if (selectedCategory.length > 0 && record.category !== selectedCategory)
                return false;
            const searchable = [record.name, record.description, record.category]
                .concat(record.tags || []).join(" ").toLowerCase();
            return needle.length === 0 || searchable.indexOf(needle) >= 0;
        });
    }
    readonly property var selectedRecord: records.length > 0
        ? records[Math.max(0, Math.min(selectedIndex, records.length - 1))] : ({})

    parent: Overlay.overlay
    anchors.centerIn: parent
    width: Math.min(1120, parent ? parent.width - 64 : 1120)
    height: Math.min(760, parent ? parent.height - 64 : 760)
    modal: true
    title: qsTr("可编辑视觉模板")
    standardButtons: Dialog.NoButton
    onRecordsChanged: selectedIndex = records.length > 0
        ? Math.min(selectedIndex, records.length - 1) : 0

    contentItem: ColumnLayout {
        spacing: 12

        RowLayout {
            Layout.fillWidth: true
            AppTextField {
                id: search
                objectName: "webTemplateSearch"
                Layout.fillWidth: true
                placeholderText: qsTr("搜索名称、用途或标签")
            }
            AppComboBox {
                id: category
                objectName: "webTemplateCategory"
                Layout.preferredWidth: 170
                model: webController.componentCategories
                textRole: "label"
                valueRole: "value"
            }
            Text {
                text: qsTr("%1 个模板").arg(root.records.length)
                color: Theme.textMuted
                font.pixelSize: Theme.fontSizeCaption
            }
        }

        RowLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 12

            ListView {
                id: templateList
                objectName: "webTemplateList"
                Layout.preferredWidth: 360
                Layout.fillHeight: true
                clip: true
                spacing: 8
                model: root.records
                ScrollBar.vertical: ScrollBar {}

                delegate: Rectangle {
                    required property int index
                    required property string componentId
                    required property string name
                    required property string category
                    required property string description
                    required property string previewBackground
                    required property string previewAccent
                    required property var aspectRatios
                    objectName: "webTemplateCard_" + componentId
                    width: templateList.width
                    height: 112
                    radius: Theme.radius
                    color: root.selectedIndex === index ? Theme.accentSoft : Theme.surfaceRaised
                    border.color: root.selectedIndex === index ? Theme.accent : Theme.border
                    function choose() { root.selectedIndex = index; }
                    RowLayout {
                        anchors.fill: parent
                        anchors.margins: 9
                        spacing: 10
                        Rectangle {
                            Layout.preferredWidth: 122
                            Layout.fillHeight: true
                            radius: Theme.radiusSmall
                            color: previewBackground
                            clip: true
                            Rectangle {
                                width: 5
                                anchors.left: parent.left
                                anchors.top: parent.top
                                anchors.bottom: parent.bottom
                                color: previewAccent
                            }
                            Text {
                                anchors.left: parent.left
                                anchors.right: parent.right
                                anchors.bottom: parent.bottom
                                anchors.margins: 10
                                text: name
                                color: previewAccent
                                font.pixelSize: 13
                                font.weight: Font.Bold
                                elide: Text.ElideRight
                            }
                        }
                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 3
                            Text {
                                Layout.fillWidth: true
                                text: name
                                color: Theme.text
                                font.pixelSize: Theme.fontSizeBodySmall
                                font.weight: Font.DemiBold
                                elide: Text.ElideRight
                            }
                            Text {
                                Layout.fillWidth: true
                                text: category + " · " + aspectRatios.join(" / ")
                                color: Theme.accentHover
                                font.pixelSize: Theme.fontSizeCaption
                                elide: Text.ElideRight
                            }
                            Text {
                                Layout.fillWidth: true
                                text: description
                                color: Theme.textMuted
                                font.pixelSize: Theme.fontSizeCaption
                                wrapMode: Text.WordWrap
                                maximumLineCount: 2
                                elide: Text.ElideRight
                            }
                        }
                    }
                    HoverHandler { onHoveredChanged: if (hovered) root.selectedIndex = parent.index }
                    TapHandler { onTapped: parent.choose() }
                }

                EmptyState {
                    anchors.fill: parent
                    visible: templateList.count === 0
                    iconText: "模"
                    title: qsTr("没有匹配的模板")
                    description: qsTr("换一个关键词或分类。")
                }
            }

            Panel {
                Layout.fillWidth: true
                Layout.fillHeight: true
                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 12
                    spacing: 10
                    Rectangle {
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        Layout.minimumHeight: 360
                        color: "#0e1014"
                        radius: Theme.radius
                        clip: true
                        WebEngineView {
                            id: templatePreview
                            objectName: "webTemplatePreview"
                            anchors.fill: parent
                            url: root.selectedRecord.previewUrl || "about:blank"
                            backgroundColor: "transparent"
                        }
                        Text {
                            anchors.centerIn: parent
                            visible: root.records.length === 0
                            text: qsTr("选择模板后在这里实时预览")
                            color: Theme.textMuted
                        }
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        ColumnLayout {
                            Layout.fillWidth: true
                            Text {
                                Layout.fillWidth: true
                                text: root.selectedRecord.name || qsTr("未选择模板")
                                color: Theme.text
                                font.pixelSize: Theme.fontSizeSection
                                font.weight: Font.DemiBold
                                elide: Text.ElideRight
                            }
                            Text {
                                Layout.fillWidth: true
                                text: root.selectedRecord.description || ""
                                color: Theme.textMuted
                                font.pixelSize: Theme.fontSizeCaption
                                wrapMode: Text.WordWrap
                            }
                        }
                        AppButton {
                            objectName: "webTemplateCancelButton"
                            text: qsTr("取消")
                            onClicked: root.close()
                        }
                        AppButton {
                            objectName: "webTemplateAddButton"
                            primary: true
                            text: qsTr("添加到播放头")
                            enabled: Boolean(root.selectedRecord.componentId)
                                && !workspaceController.readOnly
                            onClicked: {
                                webController.addComponentToTimeline(
                                    root.selectedRecord.componentId,
                                    root.playheadFrame);
                                root.close();
                            }
                        }
                    }
                }
            }
        }
    }
}

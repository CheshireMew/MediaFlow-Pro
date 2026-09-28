import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs
import QtQuick.Layouts
import "."
import "components"

AppDialog {
    id: root
    objectName: "interchangeImportDialog"
    property string timelineUrl: ""
    property var inspection: mediaflow.timelineInterchangeController.inspectionData
    parent: Overlay.overlay
    anchors.centerIn: parent
    width: Math.min(680, parent ? parent.width - 48 : 680)
    height: Math.min(660, parent ? parent.height - 48 : 660)
    modal: true
    title: qsTr("导入 FCPXML / EDL")
    standardButtons: Dialog.Close

    function isEdl() {
        return timelineUrl.toLowerCase().endsWith(".edl");
    }

    function allMappingsReady() {
        for (let index = 0; index < mappingModel.count; ++index) {
            if (!String(mappingModel.get(index).path || "").trim().length)
                return false;
        }
        return true;
    }

    function mappingPayload() {
        const result = {};
        for (let index = 0; index < mappingModel.count; ++index) {
            const row = mappingModel.get(index);
            result[String(row.key)] = String(row.path);
        }
        return result;
    }

    function inspectSelected() {
        if (!timelineUrl.length)
            return;
        mediaflow.timelineInterchangeController.inspectTimeline(
            timelineUrl, isEdl() ? frameRate.text : "");
    }

    function rebuildMappings() {
        mappingModel.clear();
        const missing = inspection.missing_sources || [];
        for (const item of missing) {
            const value = String(item);
            const divider = value.indexOf(" · ");
            mappingModel.append({
                key: divider >= 0 ? value.slice(0, divider) : value,
                label: divider >= 0 ? value.slice(divider + 3) : value,
                path: ""
            });
        }
        if (!sequenceName.text.trim().length && inspection.name)
            sequenceName.text = String(inspection.name);
    }

    FileDialog {
        id: timelineDialog
        title: qsTr("选择交换时间线")
        nameFilters: [
            qsTr("交换时间线 (*.fcpxml *.xml *.edl)"),
            qsTr("Final Cut Pro XML (*.fcpxml *.xml)"),
            qsTr("CMX 3600 EDL (*.edl)")
        ]
        onAccepted: {
            root.timelineUrl = selectedFile.toString();
            sequenceName.clear();
            root.inspectSelected();
        }
    }

    ListModel { id: mappingModel }

    Connections {
        target: mediaflow.timelineInterchangeController
        function onStateChanged() {
            if (!mediaflow.timelineInterchangeController.running)
                root.rebuildMappings();
        }
        function onImportCompleted(sequenceId) {
            root.close();
        }
    }

    contentItem: ColumnLayout {
        spacing: 9
        Text {
            Layout.fillWidth: true
            text: qsTr("导入会创建新的原生可编辑序列，不覆盖当前时间线。FCPXML 使用文件内的精确配置；EDL 默认沿用当前序列配置，可按需要指定帧率。")
            color: Theme.textMuted
            font.pixelSize: Theme.fontSizeCaption
            wrapMode: Text.WordWrap
        }
        RowLayout {
            Layout.fillWidth: true
            AppTextField {
                Layout.fillWidth: true
                readOnly: true
                text: root.timelineUrl
                placeholderText: qsTr("尚未选择时间线")
            }
            AppButton {
                objectName: "chooseInterchangeTimelineButton"
                text: qsTr("选择文件")
                onClicked: timelineDialog.open()
            }
        }
        RowLayout {
            Layout.fillWidth: true
            AppTextField {
                id: sequenceName
                objectName: "interchangeSequenceName"
                Layout.fillWidth: true
                placeholderText: qsTr("新序列名称")
            }
            AppTextField {
                id: frameRate
                objectName: "interchangeEdlFrameRate"
                Layout.preferredWidth: 150
                visible: root.isEdl()
                placeholderText: qsTr("EDL 帧率（可选）")
                onEditingFinished: root.inspectSelected()
            }
            AppButton {
                text: qsTr("重新检查")
                enabled: root.timelineUrl.length > 0
                    && !mediaflow.timelineInterchangeController.running
                onClicked: root.inspectSelected()
            }
        }
        Panel {
            Layout.fillWidth: true
            implicitHeight: inspectionSummary.implicitHeight + 18
            ColumnLayout {
                id: inspectionSummary
                anchors.fill: parent
                anchors.margins: 9
                Text {
                    Layout.fillWidth: true
                    text: mediaflow.timelineInterchangeController.running
                        ? qsTr("正在检查时间线和来源素材…")
                        : inspection.format
                            ? qsTr("%1 · %2×%3 · %4 fps · %5 个片段 · %6 个转场 · %7 条字幕")
                                .arg(String(inspection.format).toUpperCase())
                                .arg(inspection.profile.width)
                                .arg(inspection.profile.height)
                                .arg((Number(inspection.profile.fps_numerator)
                                    / Number(inspection.profile.fps_denominator)).toFixed(3))
                                .arg(inspection.clip_count)
                                .arg(inspection.transition_count)
                                .arg(inspection.caption_count)
                            : qsTr("选择文件后会先完成结构、帧率、素材路径和可保留语义检查。")
                    color: Theme.text
                    font.pixelSize: Theme.fontSizeBodySmall
                    wrapMode: Text.WordWrap
                }
                Repeater {
                    model: inspection.warnings || []
                    Text {
                        required property string modelData
                        Layout.fillWidth: true
                        text: "• " + modelData
                        color: Theme.warning
                        font.pixelSize: Theme.fontSizeCaption
                        wrapMode: Text.WordWrap
                    }
                }
            }
        }
        Text {
            Layout.fillWidth: true
            visible: mappingModel.count > 0
            text: qsTr("以下来源素材没有定位。逐项选择文件后再导入：")
            color: Theme.text
            font.weight: Font.DemiBold
        }
        ListView {
            id: mappingList
            objectName: "interchangeMissingSourceList"
            Layout.fillWidth: true
            Layout.fillHeight: true
            visible: mappingModel.count > 0
            clip: true
            spacing: 6
            model: mappingModel
            ScrollBar.vertical: AppScrollBar {}
            delegate: RowLayout {
                required property int index
                required property string key
                required property string label
                required property string path
                width: mappingList.width
                spacing: 6
                FileDialog {
                    id: sourceFileDialog
                    title: qsTr("定位 %1").arg(label)
                    onAccepted: mappingModel.setProperty(index, "path", selectedFile.toString())
                }
                Text {
                    Layout.preferredWidth: 150
                    text: label
                    color: Theme.text
                    elide: Text.ElideMiddle
                }
                AppTextField {
                    Layout.fillWidth: true
                    readOnly: true
                    text: path
                    placeholderText: qsTr("未定位")
                }
                AppButton {
                    text: qsTr("定位")
                    onClicked: sourceFileDialog.open()
                }
            }
        }
        Text {
            Layout.fillWidth: true
            visible: Boolean(inspection.error)
            text: String(inspection.error || "")
            color: Theme.danger
            wrapMode: Text.WordWrap
        }
        RowLayout {
            Layout.fillWidth: true
            Item { Layout.fillWidth: true }
            AppButton {
                objectName: "importInterchangeTimelineButton"
                primary: true
                text: mediaflow.timelineInterchangeController.importing
                    ? qsTr("导入中…") : qsTr("创建新序列")
                enabled: Boolean(inspection.format)
                    && !inspection.error
                    && root.allMappingsReady()
                    && !mediaflow.timelineInterchangeController.running
                    && !mediaflow.timelineInterchangeController.importing
                    && Boolean(mediaflow.workspaceViewController.actionCapabilities.canEdit)
                onClicked: mediaflow.timelineInterchangeController.importTimeline(
                    root.timelineUrl,
                    sequenceName.text,
                    root.isEdl() ? frameRate.text : "",
                    root.mappingPayload())
            }
        }
    }
}

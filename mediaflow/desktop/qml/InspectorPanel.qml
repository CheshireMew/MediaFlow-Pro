import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "."
import "components"

Rectangle {
    id: root
    objectName: "workspaceInspector"

    property int playheadFrame: 0
    signal editProfileRequested

    readonly property bool hasTimelineSelection:
        timelineController.selectedClipId.length > 0
        || timelineController.selectedCompoundId.length > 0
        || timelineController.selectedTransitionId.length > 0
        || webController.isWebClip
    readonly property bool hasAssetSelection:
        !hasTimelineSelection && mediaController.selectedAssetId.length > 0
    readonly property var assetData:
        hasAssetSelection ? mediaController.selectedAssetData : ({})
    readonly property string panelTitle:
        hasTimelineSelection ? qsTr("片段参数")
        : hasAssetSelection ? qsTr("素材参数") : qsTr("草稿参数")

    color: Theme.surface
    radius: Theme.radius
    border.width: 1
    border.color: Theme.borderSubtle
    clip: true

    function activeSequenceName() {
        for (let index = 0;
                index < workspaceController.sequencesModel.rowCount();
                ++index) {
            const sequence = workspaceController.sequencesModel.get(index);
            if (String(sequence.sequenceId)
                    === String(workspaceController.activeSequenceId))
                return String(sequence.name);
        }
        return qsTr("主时间线");
    }

    function assetKindLabel(kind) {
        if (kind === "video")
            return qsTr("视频");
        if (kind === "audio")
            return qsTr("音频");
        if (kind === "image")
            return qsTr("图片");
        if (kind === "subtitle")
            return qsTr("字幕");
        if (kind === "web")
            return qsTr("网页");
        return qsTr("素材");
    }

    component InfoRow: RowLayout {
        property string labelText: ""
        property string valueText: ""
        Layout.fillWidth: true
        spacing: 12

        Text {
            Layout.preferredWidth: 92
            text: parent.labelText
            color: Theme.textMuted
            font.pixelSize: Theme.fontSizeBodySmall
            verticalAlignment: Text.AlignTop
        }
        Text {
            Layout.fillWidth: true
            text: parent.valueText
            color: Theme.textSubtle
            font.pixelSize: Theme.fontSizeBodySmall
            wrapMode: Text.Wrap
            elide: Text.ElideRight
            maximumLineCount: 3
        }
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: 0

        Item {
            Layout.fillWidth: true
            Layout.preferredHeight: 50

            Text {
                anchors.left: parent.left
                anchors.leftMargin: 18
                anchors.verticalCenter: parent.verticalCenter
                text: root.panelTitle
                color: Theme.text
                font.pixelSize: Theme.fontSizeTitleSmall
                font.weight: Font.Medium
            }

            Rectangle {
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.bottom: parent.bottom
                height: 1
                color: Theme.divider
            }
        }

        StackLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            currentIndex: root.hasTimelineSelection
                ? 2 : root.hasAssetSelection ? 1 : 0

            AppScrollView {
                id: draftScroll
                clip: true
                contentWidth: availableWidth

                ColumnLayout {
                    width: draftScroll.availableWidth
                    spacing: 15

                    Item { Layout.preferredHeight: 3 }

                    Text {
                        Layout.leftMargin: 18
                        text: qsTr("项目")
                        color: Theme.text
                        font.pixelSize: Theme.fontSizeBody
                        font.weight: Font.DemiBold
                    }
                    InfoRow {
                        Layout.leftMargin: 18
                        Layout.rightMargin: 18
                        labelText: qsTr("草稿名称")
                        valueText: workspaceController.projectName
                    }
                    InfoRow {
                        Layout.leftMargin: 18
                        Layout.rightMargin: 18
                        labelText: qsTr("保存位置")
                        valueText: workspaceController.projectPath
                    }
                    InfoRow {
                        Layout.leftMargin: 18
                        Layout.rightMargin: 18
                        labelText: qsTr("色彩空间")
                        valueText: workspaceController.colorMode
                            === "hdr10_bt2020_pq" ? "HDR10 · BT.2020 PQ"
                            : "Rec.709 SDR"
                    }
                    InfoRow {
                        Layout.leftMargin: 18
                        Layout.rightMargin: 18
                        labelText: qsTr("项目状态")
                        valueText: workspaceController.readOnly
                            ? qsTr("只读") : qsTr("可编辑")
                    }

                    Rectangle {
                        Layout.fillWidth: true
                        Layout.preferredHeight: 1
                        color: Theme.divider
                    }

                    Text {
                        Layout.leftMargin: 18
                        text: qsTr("时间线")
                        color: Theme.text
                        font.pixelSize: Theme.fontSizeBody
                        font.weight: Font.DemiBold
                    }
                    InfoRow {
                        Layout.leftMargin: 18
                        Layout.rightMargin: 18
                        labelText: qsTr("时间线名称")
                        valueText: root.activeSequenceName()
                    }
                    InfoRow {
                        Layout.leftMargin: 18
                        Layout.rightMargin: 18
                        labelText: qsTr("规格")
                        valueText: workspaceController.profileLabel
                    }
                    InfoRow {
                        Layout.leftMargin: 18
                        Layout.rightMargin: 18
                        labelText: qsTr("声道")
                        valueText: workspaceController.profileAudioChannels === 1
                            ? qsTr("单声道") : qsTr("立体声")
                    }
                    InfoRow {
                        Layout.leftMargin: 18
                        Layout.rightMargin: 18
                        labelText: qsTr("总帧数")
                        valueText: String(workspaceController.timelineDurationFrames)
                    }

                    Item { Layout.fillHeight: true; Layout.minimumHeight: 12 }

                    AppButton {
                        Layout.fillWidth: true
                        Layout.leftMargin: 18
                        Layout.rightMargin: 18
                        text: qsTr("修改时间线设置")
                        enabled: workspaceController.actionCapabilities.canEdit
                        onClicked: root.editProfileRequested()
                    }
                    Item { Layout.preferredHeight: 16 }
                }
            }

            AppScrollView {
                id: assetScroll
                clip: true
                contentWidth: availableWidth

                ColumnLayout {
                    width: assetScroll.availableWidth
                    spacing: 15

                    Item { Layout.preferredHeight: 3 }

                    Text {
                        Layout.fillWidth: true
                        Layout.leftMargin: 18
                        Layout.rightMargin: 18
                        text: root.assetData.name || qsTr("素材")
                        color: Theme.text
                        font.pixelSize: Theme.fontSizeBody
                        font.weight: Font.DemiBold
                        elide: Text.ElideRight
                    }
                    InfoRow {
                        Layout.leftMargin: 18
                        Layout.rightMargin: 18
                        labelText: qsTr("类型")
                        valueText: root.assetKindLabel(
                            String(root.assetData.kind || ""))
                    }
                    InfoRow {
                        Layout.leftMargin: 18
                        Layout.rightMargin: 18
                        labelText: qsTr("状态")
                        valueText: root.assetData.status === "online"
                            ? qsTr("可用") : qsTr("离线")
                    }
                    InfoRow {
                        Layout.leftMargin: 18
                        Layout.rightMargin: 18
                        labelText: qsTr("分辨率")
                        valueText: Number(root.assetData.width || 0) > 0
                            ? String(root.assetData.width) + " × "
                                + String(root.assetData.height)
                            : "—"
                    }
                    InfoRow {
                        Layout.leftMargin: 18
                        Layout.rightMargin: 18
                        labelText: qsTr("持续帧")
                        valueText: String(root.assetData.durationFrames || 0)
                    }
                    InfoRow {
                        Layout.leftMargin: 18
                        Layout.rightMargin: 18
                        labelText: qsTr("文件位置")
                        valueText: root.assetData.path || "—"
                    }
                    InfoRow {
                        Layout.leftMargin: 18
                        Layout.rightMargin: 18
                        labelText: qsTr("代理")
                        valueText: root.assetData.proxyReady
                            ? qsTr("已就绪") : qsTr("未生成")
                    }
                    InfoRow {
                        Layout.leftMargin: 18
                        Layout.rightMargin: 18
                        labelText: qsTr("波形")
                        valueText: root.assetData.waveformReady
                            ? qsTr("已就绪") : qsTr("未生成")
                    }
                    Item { Layout.fillHeight: true }
                }
            }

            EditPanel {
                playheadFrame: root.playheadFrame
            }
        }
    }
}

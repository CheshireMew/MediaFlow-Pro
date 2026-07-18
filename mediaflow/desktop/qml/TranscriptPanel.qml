import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Dialogs
import "."
import "components"

ColumnLayout {
    id: root
    spacing: 9
    property bool showSearch: false
    property var searchMatches: []
    property int searchMatchIndex: -1

    function refreshSearch() {
        root.searchMatches = subtitleController.findSubtitleMatches(
            findText.text, matchCase.checked)
        if (root.searchMatches.length === 0)
            root.searchMatchIndex = -1
        else if (root.searchMatchIndex < 0 || root.searchMatchIndex >= root.searchMatches.length)
            root.searchMatchIndex = 0
    }

    function activateSearchMatch(index) {
        if (root.searchMatches.length === 0)
            return
        const count = root.searchMatches.length
        root.searchMatchIndex = ((index % count) + count) % count
        subtitleController.selectSubtitleSegment(
            String(root.searchMatches[root.searchMatchIndex].segmentId), false)
    }

    function loadSelectedSegment() {
        const data = subtitleController.selectedSubtitleSegmentData
        segmentStart.value = Number(data.startFrame || 0)
        segmentEnd.value = Number(data.endFrame || 1)
        segmentText.text = data.text || ""
    }

    Connections {
        target: subtitleController
        function onSelectionChanged() { root.loadSelectedSegment() }
    }

    FileDialog {
        id: exportSubtitleDialog
        title: qsTr("导出字幕文档")
        fileMode: FileDialog.SaveFile
        nameFilters: [qsTr("SRT 字幕 (*.srt)")]
        onAccepted: subtitleController.exportSubtitleDocument(
            subtitleController.selectedDocumentId, selectedFile.toString())
    }

    RowLayout {
        Layout.fillWidth: true
        Text {
            text: qsTr("转录与字幕")
            color: Theme.text
            font.pixelSize: Theme.fontSizeSection
            font.weight: Font.DemiBold
        }
        Item { Layout.fillWidth: true }
        AppButton {
            text: qsTr("开始转录")
            primary: true
            enabled: mediaController.selectedAssetId.length > 0
            onClicked: subtitleController.transcribeSelectedAsset()
        }
    }

    Text {
        Layout.fillWidth: true
        text: mediaController.selectedAssetId.length > 0
              ? qsTr("当前素材已选中。也可以直接导入 SRT、WebVTT、ASS 或 SSA 字幕。")
              : qsTr("请先到“媒体”模式选择素材，或导入字幕文件。")
        color: Theme.textMuted
        font.pixelSize: Theme.fontSizeCaption
        wrapMode: Text.WordWrap
    }

    Panel {
        Layout.fillWidth: true
        implicitHeight: 104
        ColumnLayout {
            anchors.fill: parent
            anchors.margins: 8
            spacing: 6
            Text {
                text: qsTr("素材选区转录")
                color: Theme.text
                font.pixelSize: Theme.fontSizeBodySmall
                font.weight: Font.DemiBold
            }
            RowLayout {
                Layout.fillWidth: true
                Text {
                    text: qsTr("开始帧")
                    color: Theme.textMuted
                    font.pixelSize: Theme.fontSizeCaption
                }
                AppSpinBox {
                    id: regionStart
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
                    id: regionEnd
                    Layout.fillWidth: true
                    from: 1
                    to: Math.max(1, Number(mediaController.selectedAssetData.durationFrames || 2147483647))
                    value: Math.max(1, Math.min(to, 60))
                    editable: true
                }
            }
            RowLayout {
                Layout.fillWidth: true
                AppButton {
                    Layout.fillWidth: true
                    text: qsTr("转录选区")
                    enabled: mediaController.selectedAssetId.length > 0 && regionEnd.value > regionStart.value
                    onClicked: subtitleController.transcribeRegion(
                        regionStart.value, regionEnd.value, false)
                }
                AppButton {
                    Layout.fillWidth: true
                    primary: true
                    text: qsTr("转录后翻译")
                    enabled: mediaController.selectedAssetId.length > 0 && regionEnd.value > regionStart.value
                    onClicked: subtitleController.transcribeRegion(
                        regionStart.value, regionEnd.value, true)
                }
            }
        }
    }

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
        clip: true
        spacing: 5
        model: subtitleController.subtitleDocumentsModel
        delegate: Rectangle {
            required property string documentId
            required property string language
            required property bool isSource
            required property int segmentCount
            width: documentList.width
            height: 48
            radius: Theme.radiusSmall
            color: subtitleController.selectedDocumentId === documentId
                   ? Theme.accentSoft : docMouse.containsMouse
                   ? Theme.surfaceHover : Theme.surfaceRaised
            border.color: subtitleController.selectedDocumentId === documentId
                          ? Theme.accent : Theme.border
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
                Item { Layout.fillWidth: true }
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
                onClicked: subtitleController.selectSubtitleDocument(documentId)
            }
        }
    }

    TabBar {
        id: subtitleTabs
        Layout.fillWidth: true
        TabButton { text: qsTr("文档编辑") }
        TabButton { text: qsTr("序列字幕") }
    }

    StackLayout {
        Layout.fillWidth: true
        Layout.fillHeight: true
        currentIndex: subtitleTabs.currentIndex

        ColumnLayout {
            spacing: 7

            RowLayout {
                Layout.fillWidth: true
                AppButton {
                    text: qsTr("添加")
                    enabled: subtitleController.selectedDocumentId.length > 0
                    onClicked: subtitleController.addSubtitleSegment()
                }
                AppButton {
                    text: qsTr("合并")
                    enabled: subtitleController.selectedSubtitleSegmentIds.length >= 2
                    onClicked: subtitleController.mergeSelectedSubtitleSegments()
                }
                AppButton {
                    text: qsTr("删除")
                    enabled: subtitleController.selectedSubtitleSegmentIds.length > 0
                    onClicked: subtitleController.deleteSelectedSubtitleSegments()
                }
                AppButton {
                    text: qsTr("查找替换")
                    checkable: true
                    checked: root.showSearch
                    onClicked: root.showSearch = checked
                }
                Item { Layout.fillWidth: true }
                AppButton {
                    text: qsTr("导出 SRT")
                    enabled: subtitleController.selectedDocumentId.length > 0
                    onClicked: exportSubtitleDialog.open()
                }
            }

            RowLayout {
                Layout.fillWidth: true
                AppButton {
                    Layout.fillWidth: true
                    text: qsTr("翻译所选")
                    enabled: subtitleController.selectedSubtitleSegmentIds.length > 0
                    onClicked: subtitleController.translateSelectedSubtitleSegments()
                }
                AppButton {
                    Layout.fillWidth: true
                    text: qsTr("复制 SRT")
                    enabled: subtitleController.selectedSubtitleSegmentIds.length > 0
                    onClicked: subtitleController.copySelectedSubtitleSegments()
                }
                AppButton {
                    Layout.fillWidth: true
                    text: qsTr("粘贴替换")
                    enabled: subtitleController.selectedSubtitleSegmentIds.length > 0
                    onClicked: subtitleController.pasteReplaceSelectedSubtitleSegments()
                }
                AppButton {
                    Layout.fillWidth: true
                    text: qsTr("打开文件夹")
                    enabled: subtitleController.selectedDocumentId.length > 0
                    onClicked: subtitleController.openSubtitleFolder()
                }
            }

            Panel {
                Layout.fillWidth: true
                implicitHeight: root.showSearch ? 152 : 0
                visible: root.showSearch
                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 8
                    spacing: 6
                    RowLayout {
                        Layout.fillWidth: true
                        AppTextField {
                            id: findText
                            Layout.fillWidth: true
                            placeholderText: qsTr("查找")
                            onTextChanged: root.refreshSearch()
                        }
                        AppTextField {
                            id: replaceText
                            Layout.fillWidth: true
                            placeholderText: qsTr("替换为")
                        }
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        AppCheckBox {
                            id: matchCase
                            text: qsTr("区分大小写")
                            onToggled: root.refreshSearch()
                        }
                        Text {
                            text: qsTr("找到 %1 处").arg(root.searchMatches.length)
                            color: Theme.textMuted
                            font.pixelSize: Theme.fontSizeCaption
                        }
                        Item { Layout.fillWidth: true }
                        AppButton {
                            text: qsTr("全部替换")
                            primary: true
                            enabled: findText.text.length > 0 && root.searchMatches.length > 0
                            onClicked: {
                                subtitleController.replaceSubtitleText(
                                    findText.text, replaceText.text, matchCase.checked)
                                root.searchMatches = subtitleController.findSubtitleMatches(
                                    findText.text, matchCase.checked)
                            }
                        }
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        AppButton {
                            text: qsTr("上一个")
                            enabled: root.searchMatches.length > 0
                            onClicked: root.activateSearchMatch(root.searchMatchIndex - 1)
                        }
                        AppButton {
                            text: qsTr("下一个")
                            enabled: root.searchMatches.length > 0
                            onClicked: root.activateSearchMatch(root.searchMatchIndex + 1)
                        }
                        Text {
                            Layout.fillWidth: true
                            text: root.searchMatchIndex >= 0
                                ? qsTr("当前 %1 / %2").arg(root.searchMatchIndex + 1)
                                    .arg(root.searchMatches.length)
                                : qsTr("没有当前匹配")
                            color: Theme.textMuted
                            font.pixelSize: Theme.fontSizeCaption
                        }
                        AppButton {
                            text: qsTr("替换当前")
                            primary: true
                            enabled: root.searchMatchIndex >= 0
                            onClicked: {
                                const match = root.searchMatches[root.searchMatchIndex]
                                subtitleController.replaceSubtitleMatch(
                                    String(match.segmentId), Number(match.start), Number(match.end),
                                    findText.text, replaceText.text, matchCase.checked)
                                Qt.callLater(function() {
                                    root.refreshSearch()
                                    root.activateSearchMatch(root.searchMatchIndex)
                                })
                            }
                        }
                    }
                }
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
                    text: subtitleController.selectedSubtitleSegmentIds.length > 1
                          ? qsTr("已选 %1 条").arg(subtitleController.selectedSubtitleSegmentIds.length)
                          : ""
                    color: Theme.accentHover
                    font.pixelSize: Theme.fontSizeCaption
                }
                Item { Layout.fillWidth: true }
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
                    enabled: subtitleController.selectedDocumentId.length > 0
                    onClicked: subtitleController.smartSplitSubtitleDocument(smartSplitLimit.value)
                }
                AppButton {
                    text: qsTr("修复重叠")
                    enabled: subtitleController.selectedDocumentId.length > 0
                    onClicked: subtitleController.fixSubtitleOverlaps()
                }
            }

            ListView {
                id: segmentList
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                spacing: 5
                model: subtitleController.subtitleSegmentsModel
                delegate: Rectangle {
                    required property string segmentId
                    required property int startFrame
                    required property int endFrame
                    required property string text
                    required property bool hasOverlap
                    width: segmentList.width
                    height: segmentText.implicitHeight + 31
                    radius: Theme.radiusSmall
                    color: subtitleController.isSubtitleSegmentSelected(segmentId)
                           ? Theme.accentSoft : segmentMouse.containsMouse
                           ? Theme.surfaceHover : Theme.surfaceRaised
                    border.color: hasOverlap ? Theme.danger
                                  : subtitleController.isSubtitleSegmentSelected(segmentId)
                                  ? Theme.accent : Theme.border
                    ColumnLayout {
                        anchors.fill: parent
                        anchors.margins: 8
                        spacing: 3
                        Text {
                            text: startFrame + " – " + endFrame
                            color: hasOverlap ? Theme.danger : Theme.textMuted
                            font.pixelSize: Theme.fontSizeCaption
                            font.family: Theme.monoFontFamily
                        }
                        Text {
                            id: segmentText
                            Layout.fillWidth: true
                            text: parent.parent.text
                            color: Theme.text
                            font.pixelSize: Theme.fontSizeCaption
                            wrapMode: Text.WordWrap
                        }
                    }
                    MouseArea {
                        id: segmentMouse
                        anchors.fill: parent
                        hoverEnabled: true
                        onClicked: mouse => subtitleController.selectSubtitleSegment(
                            segmentId, (mouse.modifiers & Qt.ControlModifier) !== 0)
                    }
                }
                EmptyState {
                    anchors.fill: parent
                    visible: segmentList.count === 0
                    iconText: "字"
                    title: qsTr("还没有字幕")
                    description: qsTr("转录媒体或导入 SRT 后，可以在这里逐条编辑。")
                }
            }

            Panel {
                Layout.fillWidth: true
                implicitHeight: 178
                visible: subtitleController.selectedSubtitleSegmentIds.length === 1
                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 8
                    spacing: 6
                    RowLayout {
                        Layout.fillWidth: true
                        Text {
                            text: qsTr("开始帧")
                            color: Theme.textMuted
                            font.pixelSize: Theme.fontSizeCaption
                        }
                        AppSpinBox {
                            id: segmentStart
                            Layout.fillWidth: true
                            from: 0
                            to: 2147483647
                            editable: true
                        }
                        Text {
                            text: qsTr("结束帧")
                            color: Theme.textMuted
                            font.pixelSize: Theme.fontSizeCaption
                        }
                        AppSpinBox {
                            id: segmentEnd
                            Layout.fillWidth: true
                            from: 1
                            to: 2147483647
                            editable: true
                        }
                    }
                    TextArea {
                        id: segmentText
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        color: Theme.text
                        wrapMode: TextEdit.Wrap
                        selectByMouse: true
                        background: Rectangle {
                            color: Theme.window
                            border.color: segmentText.activeFocus ? Theme.accent : Theme.border
                            radius: Theme.radiusSmall
                        }
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        AppButton {
                            Layout.fillWidth: true
                            text: qsTr("按中点拆分")
                            onClicked: subtitleController.splitSubtitleSegment(
                                subtitleController.selectedSubtitleSegmentId, -1)
                        }
                        AppButton {
                            Layout.fillWidth: true
                            primary: true
                            text: qsTr("保存修改")
                            onClicked: subtitleController.updateSubtitleSegment(
                                subtitleController.selectedSubtitleSegmentId,
                                segmentStart.value,
                                segmentEnd.value,
                                segmentText.text)
                        }
                    }
                }
            }
        }

        ColumnLayout {
            spacing: 7
            RowLayout {
                Layout.fillWidth: true
                Text {
                    text: qsTr("序列字幕")
                    color: Theme.text
                    font.pixelSize: Theme.fontSizeBodySmall
                    font.weight: Font.DemiBold
                }
                Item { Layout.fillWidth: true }
                AppButton {
                    text: qsTr("放入当前序列")
                    primary: true
                    enabled: subtitleController.selectedDocumentId.length > 0
                    onClicked: subtitleController.placeSubtitleDocument(
                        subtitleController.selectedDocumentId)
                }
            }
            ListView {
                id: placementList
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                spacing: 4
                model: subtitleController.subtitlePlacementsModel
                delegate: Rectangle {
                    required property string placementId
                    required property int startFrame
                    required property int endFrame
                    required property string text
                    required property bool hasOverride
                    width: placementList.width
                    height: 48
                    radius: Theme.radiusSmall
                    color: subtitleController.selectedSubtitlePlacementId === placementId
                           ? Theme.accentSoft : placementMouse.containsMouse
                           ? Theme.surfaceHover : Theme.surfaceRaised
                    border.color: hasOverride ? Theme.accent : Theme.border
                    RowLayout {
                        anchors.fill: parent
                        anchors.margins: 7
                        Text {
                            text: startFrame + "–" + endFrame
                            color: Theme.textMuted
                            font.pixelSize: Theme.fontSizeCaption
                        }
                        Text {
                            Layout.fillWidth: true
                            text: parent.parent.text
                            color: Theme.text
                            elide: Text.ElideRight
                            font.pixelSize: Theme.fontSizeCaption
                        }
                        Text {
                            visible: hasOverride
                            text: qsTr("序列覆盖")
                            color: Theme.accentHover
                            font.pixelSize: Theme.fontSizeCaption
                        }
                    }
                    MouseArea {
                        id: placementMouse
                        anchors.fill: parent
                        hoverEnabled: true
                        onClicked: subtitleController.selectSubtitlePlacement(placementId)
                    }
                }
                EmptyState {
                    anchors.fill: parent
                    visible: placementList.count === 0
                    iconText: "轨"
                    title: qsTr("序列中还没有字幕")
                    description: qsTr("选择字幕文档并放入当前序列。")
                }
            }
            Panel {
                Layout.fillWidth: true
                implicitHeight: 152
                visible: subtitleController.selectedSubtitlePlacementId.length > 0
                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 8
                    spacing: 6
                    TextArea {
                        id: placementText
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        text: subtitleController.selectedSubtitlePlacementData.text || ""
                        color: Theme.text
                        wrapMode: TextEdit.Wrap
                        background: Rectangle {
                            color: Theme.window
                            border.color: Theme.border
                            radius: Theme.radiusSmall
                        }
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        AppButton {
                            Layout.fillWidth: true
                            primary: true
                            text: qsTr("保存为序列覆盖")
                            onClicked: subtitleController.updateSubtitlePlacementText(
                                subtitleController.selectedSubtitlePlacementId,
                                placementText.text,
                                false)
                        }
                        AppButton {
                            Layout.fillWidth: true
                            text: qsTr("应用到文档")
                            onClicked: subtitleController.updateSubtitlePlacementText(
                                subtitleController.selectedSubtitlePlacementId,
                                placementText.text,
                                true)
                        }
                    }
                }
            }
        }
    }
}

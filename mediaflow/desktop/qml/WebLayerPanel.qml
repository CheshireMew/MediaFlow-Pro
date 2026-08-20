import QtQuick
import QtQuick.Dialogs
import QtQuick.Layouts
import "."
import "components"

Panel {
    id: root
    objectName: "webLayerPanel"
    Layout.fillWidth: true
    implicitHeight: content.implicitHeight + 22
    visible: mediaflow.webController.isWebClip
    enabled: canEdit
    opacity: canEdit ? 1.0 : 0.72
    readonly property bool canEdit:
        Boolean(mediaflow.workspaceViewController.actionCapabilities.canEdit)
    property int playheadFrame: 0
    property string snapshotFieldId: ""
    signal seekRequested(int frame)
    Component.onCompleted: mediaflow.webTimelineController.setActiveFrame(playheadFrame)
    onPlayheadFrameChanged: mediaflow.webTimelineController.setActiveFrame(playheadFrame)

    FileDialog {
        id: snapshotDialog
        title: qsTr("导入本地数据快照")
        nameFilters: [qsTr("数据文件 (*.json *.csv)")]
        onAccepted: mediaflow.webController.importDataSnapshot(selectedFile, root.snapshotFieldId)
    }
    FileDialog {
        id: rebindDialog
        title: qsTr("选择新版网页包")
        nameFilters: [qsTr("Editable media manifest (editable-media.json)")]
        onAccepted: mediaflow.webDeliveryController.inspectRebind(selectedFile)
    }
    FileDialog {
        id: batchSourceDialog
        title: qsTr("选择批量记录")
        nameFilters: [qsTr("批量数据 (*.json *.csv)")]
        onAccepted: mediaflow.webDeliveryController.createBatchVariantsFromFile(
            selectedFile, batchBindings.text, batchNameTemplate.text)
    }
    FileDialog {
        id: webExportDialog
        title: qsTr("导出网页片段")
        fileMode: FileDialog.SaveFile
        nameFilters: exportFormat.currentIndex >= 0
            ? [exportFormat.model[exportFormat.currentIndex].filter]
            : []
        defaultSuffix: exportFormat.currentIndex >= 0
            ? exportFormat.model[exportFormat.currentIndex].suffix
            : ""
        onAccepted: mediaflow.webDeliveryController.exportSelected(
            selectedFile, String(exportFormat.currentValue),
            mediaflow.webTimelineController.timeMsForFrame(root.playheadFrame), exportBackground.text, true)
    }

    ColumnLayout {
        id: content
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.margins: 11
        spacing: 8

        Text {
            text: mediaflow.webController.componentData.name
                ? qsTr("网页组件 · ") + mediaflow.webController.componentData.name
                : qsTr("网页图层")
            color: Theme.text
            font.pixelSize: Theme.fontSizeBodySmall
            font.weight: Font.DemiBold
        }

        Text {
            Layout.fillWidth: true
            visible: !root.canEdit
            text: qsTr("项目以只读方式打开，网页参数仅供查看")
            color: Theme.warning
            font.pixelSize: Theme.fontSizeCaption
            wrapMode: Text.WordWrap
        }

        AppComboBox {
            id: variantSelector
            Layout.fillWidth: true
            textRole: "name"
            valueRole: "id"
            model: mediaflow.webController.variantOptions
            currentIndex: Math.max(0, indexOfValue(
                mediaflow.webController.activeVariantId))
            onActivated: mediaflow.webController.selectVariant(String(currentValue || ""))
        }

        RowLayout {
            Layout.fillWidth: true
            AppButton {
                Layout.fillWidth: true
                primary: mediaflow.webController.editMode
                text: qsTr("网页编辑")
                onClicked: mediaflow.webController.setEditMode(true)
            }
            AppButton {
                Layout.fillWidth: true
                primary: !mediaflow.webController.editMode
                text: qsTr("合成预览")
                onClicked: mediaflow.webController.setEditMode(false)
            }
        }

        ListView {
            Layout.fillWidth: true
            Layout.preferredHeight: Math.min(210, Math.max(44, contentHeight))
            clip: true
            spacing: 4
            model: mediaflow.webController.layersModel
            delegate: Rectangle {
                required property string layerId
                required property string name
                required property string kind
                required property string parentId
                required property bool layerVisible
                required property bool allFieldsLocked
                required property int keyframeCount
                width: ListView.view.width
                height: 38
                radius: Theme.radiusSmall
                color: mediaflow.webController.selectedLayerId === layerId ? Theme.accentSoft : Theme.surfaceRaised
                border.color: mediaflow.webController.selectedLayerId === layerId ? Theme.accent : Theme.border
                RowLayout {
                    anchors.fill: parent
                    anchors.leftMargin: parentId.length > 0 ? 20 : 8
                    anchors.rightMargin: 7
                    Text {
                        Layout.fillWidth: true
                        text: name
                        color: layerVisible ? Theme.text : Theme.textMuted
                        font.pixelSize: Theme.fontSizeCaption
                        elide: Text.ElideRight
                    }
                    RowLayout {
                        spacing: 3
                        Text {
                            text: kind
                            color: Theme.textMuted
                            font.pixelSize: 10
                        }
                        AppIcon {
                            visible: keyframeCount > 0
                            Layout.preferredWidth: 9
                            Layout.preferredHeight: 9
                            iconName: "keyframe"
                            iconColor: Theme.cut
                        }
                        Text {
                            visible: keyframeCount > 0
                            text: keyframeCount
                            color: Theme.textMuted
                            font.pixelSize: 10
                        }
                    }
                    AppIconButton {
                        implicitWidth: 30
                        implicitHeight: 28
                        iconSize: 15
                        iconName: layerVisible ? "eye" : "eye-off"
                        flat: false
                        Accessible.name: layerVisible ? qsTr("隐藏图层") : qsTr("显示图层")
                        toolTipText: Accessible.name
                        onClicked: mediaflow.webController.updateLayer(layerId, {visible: !layerVisible})
                    }
                    AppIconButton {
                        implicitWidth: 30
                        implicitHeight: 28
                        iconSize: 15
                        iconName: allFieldsLocked ? "lock" : "unlock"
                        flat: false
                        Accessible.name: allFieldsLocked ? qsTr("解锁图层") : qsTr("锁定图层")
                        toolTipText: Accessible.name
                        onClicked: mediaflow.webController.setLayerLocked(layerId, !allFieldsLocked)
                    }
                }
                TapHandler {
                    onTapped: mediaflow.webController.selectLayer(parent.layerId)
                }
            }
        }

        Text {
            Layout.fillWidth: true
            visible: mediaflow.webTimelineController.timelineItemsData.length > 0
            text: qsTr("场景时间线")
            color: Theme.text
            font.pixelSize: Theme.fontSizeCaption
            font.weight: Font.DemiBold
        }

        WebTimelineEditor {
            Layout.fillWidth: true
            visible: mediaflow.webTimelineController.timelineItemsData.length > 0
            playheadFrame: root.playheadFrame
            onSeekRequested: function(frame) { root.seekRequested(frame); }
        }

        Text {
            Layout.fillWidth: true
            text: mediaflow.webController.selectedLayerId.length > 0
                ? qsTr("图层属性") : qsTr("选择一个图层")
            color: Theme.text
            font.pixelSize: Theme.fontSizeCaption
            font.weight: Font.DemiBold
        }

        Repeater {
            model: mediaflow.webController.selectedLayerDescriptors
            WebPropertyEditor {
                required property var modelData
                descriptor: modelData
                playheadFrame: root.playheadFrame
            }
        }

        Text {
            Layout.fillWidth: true
            visible: mediaflow.webController.parameterDescriptors.length > 0
            text: qsTr("动画与效果参数")
            color: Theme.text
            font.pixelSize: Theme.fontSizeCaption
            font.weight: Font.DemiBold
        }

        Repeater {
            model: mediaflow.webController.parameterDescriptors
            WebPropertyEditor {
                required property var modelData
                descriptor: modelData
                playheadFrame: root.playheadFrame
            }
        }

        Text {
            Layout.fillWidth: true
            visible: mediaflow.webController.themeDescriptors.length > 0
            text: qsTr("品牌主题")
            color: Theme.text
            font.pixelSize: Theme.fontSizeCaption
            font.weight: Font.DemiBold
        }

        Repeater {
            model: mediaflow.webController.themeDescriptors
            WebPropertyEditor {
                required property var modelData
                descriptor: modelData
                playheadFrame: root.playheadFrame
            }
        }

        Text {
            Layout.fillWidth: true
            text: qsTr("批量变体")
            color: Theme.text
            font.pixelSize: Theme.fontSizeCaption
            font.weight: Font.DemiBold
        }
        AppTextField {
            id: batchRecords
            Layout.fillWidth: true
            placeholderText: qsTr("记录 JSON，例如 [{\"name\":\"Ada\"}]")
        }
        AppTextField {
            id: batchBindings
            Layout.fillWidth: true
            placeholderText: qsTr("绑定 JSON，例如 {\"name\":\"scenes.opening.layers.title.content\"}")
        }
        AppTextField {
            id: batchNameTemplate
            Layout.fillWidth: true
            text: qsTr("版本 {index}")
            placeholderText: qsTr("序列名称模板")
        }
        AppButton {
            Layout.fillWidth: true
            text: qsTr("生成批量短序列")
            enabled: batchRecords.text.length > 0 && batchBindings.text.length > 0
            onClicked: mediaflow.webDeliveryController.createBatchVariants(
                batchRecords.text, batchBindings.text, batchNameTemplate.text)
        }
        AppButton {
            Layout.fillWidth: true
            text: qsTr("从 CSV/JSON 生成")
            enabled: batchBindings.text.length > 0
            onClicked: batchSourceDialog.open()
        }

        Text {
            Layout.fillWidth: true
            text: qsTr("网页素材更新")
            color: Theme.text
            font.pixelSize: Theme.fontSizeCaption
            font.weight: Font.DemiBold
        }
        AppButton {
            Layout.fillWidth: true
            text: qsTr("检查新版网页包")
            onClicked: rebindDialog.open()
        }
        AppButton {
            Layout.fillWidth: true
            visible: String(mediaflow.webDeliveryController.rebindPlan.new_source_hash || "").length > 0
            text: qsTr("复制换版检查为 CLI 请求")
            onClicked: mediaflow.automationController.copyWebRebindPlanRequest()
        }
        Text {
            Layout.fillWidth: true
            visible: String(mediaflow.webDeliveryController.rebindPlan.new_source_hash || "").length > 0
            text: qsTr("新增 %1 · 保留 %2 · 移除 %3 · 冲突 %4")
                .arg((mediaflow.webDeliveryController.rebindPlan.added_layers || []).length)
                .arg((mediaflow.webDeliveryController.rebindPlan.retained_layers || []).length)
                .arg((mediaflow.webDeliveryController.rebindPlan.removed_layers || []).length)
                .arg((mediaflow.webDeliveryController.rebindPlan.conflicts || []).length)
            color: (mediaflow.webDeliveryController.rebindPlan.conflicts || []).length > 0
                ? Theme.warning : Theme.textMuted
            font.pixelSize: Theme.fontSizeCaption
            wrapMode: Text.WordWrap
        }

        Repeater {
            model: mediaflow.webDeliveryController.rebindPlan.conflicts || []
            ColumnLayout {
                required property var modelData
                Layout.fillWidth: true
                spacing: 4
                Text {
                    Layout.fillWidth: true
                    text: String(modelData.path) + "\n" + String(modelData.message)
                    color: Theme.warning
                    font.pixelSize: 10
                    wrapMode: Text.WrapAnywhere
                }
                AppComboBox {
                    Layout.fillWidth: true
                    model: modelData.allowed_resolutions || []
                    currentIndex: {
                        const selected = (mediaflow.webDeliveryController.rebindPlan.resolutions || {})[
                            String(modelData.path)];
                        return selected ? Math.max(0, model.indexOf(selected)) : -1;
                    }
                    displayText: currentIndex < 0
                        ? qsTr("请选择此项如何处理") : currentText
                    onActivated: mediaflow.webDeliveryController.setRebindResolution(
                        String(modelData.path), String(currentValue))
                }
            }
        }

        AppButton {
            Layout.fillWidth: true
            visible: String(mediaflow.webDeliveryController.rebindPlan.new_source_hash || "").length > 0
            primary: true
            text: qsTr("按已审阅计划重新绑定")
            enabled: Object.keys(
                mediaflow.webDeliveryController.rebindPlan.resolutions || {}).length
                === (mediaflow.webDeliveryController.rebindPlan.conflicts || []).length
            onClicked: mediaflow.webDeliveryController.commitRebind()
        }
        AppButton {
            Layout.fillWidth: true
            visible: String(mediaflow.webDeliveryController.rebindPlan.new_source_hash || "").length > 0
            text: qsTr("复制换版提交为 CLI 请求")
            enabled: Object.keys(
                mediaflow.webDeliveryController.rebindPlan.resolutions || {}).length
                === (mediaflow.webDeliveryController.rebindPlan.conflicts || []).length
            onClicked: mediaflow.automationController.copyWebRebindCommitRequest()
        }

        Text {
            Layout.fillWidth: true
            text: qsTr("同源多格式导出")
            color: Theme.text
            font.pixelSize: Theme.fontSizeCaption
            font.weight: Font.DemiBold
        }
        RowLayout {
            Layout.fillWidth: true
            AppComboBox {
                id: exportFormat
                Layout.fillWidth: true
                textRole: "label"
                valueRole: "value"
                model: mediaflow.webDeliveryController.exportFormatOptions
            }
            AppTextField {
                id: exportBackground
                Layout.fillWidth: true
                text: "#000000"
                placeholderText: qsTr("视频背景")
            }
        }
        AppButton {
            Layout.fillWidth: true
            text: qsTr("选择位置并导出")
            onClicked: webExportDialog.open()
        }

        Text {
            Layout.fillWidth: true
            visible: mediaflow.webController.dataDescriptors.length > 0
            text: qsTr("数据与图表")
            color: Theme.text
            font.pixelSize: Theme.fontSizeCaption
            font.weight: Font.DemiBold
        }
        Repeater {
            model: mediaflow.webController.dataDescriptors
            ColumnLayout {
                required property var modelData
                Layout.fillWidth: true
                RowLayout {
                    Layout.fillWidth: true
                    WebPropertyEditor {
                        Layout.fillWidth: true
                        descriptor: modelData
                        playheadFrame: root.playheadFrame
                    }
                    AppButton {
                        text: qsTr("文件")
                        onClicked: {
                            root.snapshotFieldId = modelData.source_id;
                            snapshotDialog.open();
                        }
                    }
                }
            }
        }
    }
}

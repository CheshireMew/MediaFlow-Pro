import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs
import QtQuick.Layouts
import "."
import "components"

Panel {
    id: root
    objectName: "webLayerPanel"
    Layout.fillWidth: true
    implicitHeight: content.implicitHeight + 22
    visible: webController.isWebClip
    property int playheadFrame: 0
    property string snapshotFieldId: ""

    function editable(name) {
        const fields = webController.selectedLayerData.editable || [];
        return fields.indexOf(name) >= 0;
    }

    function fieldValue(name) {
        const row = webController.selectedLayerData;
        const keys = {
            "font_family": "fontFamily", "font_size": "fontSize", "z_index": "zIndex",
            "visible": "layerVisible", "enter_ms": "enterMs", "exit_ms": "exitMs",
            "delay_ms": "delayMs", "duration_ms": "durationMs"
        };
        return row[keys[name] || name] ?? "";
    }

    FileDialog {
        id: snapshotDialog
        title: qsTr("导入本地数据快照")
        nameFilters: [qsTr("数据文件 (*.json *.csv)")]
        onAccepted: webController.importDataSnapshot(selectedFile, root.snapshotFieldId)
    }
    FileDialog {
        id: rebindDialog
        title: qsTr("选择新版网页包")
        nameFilters: [qsTr("Editable media manifest (editable-media.json)")]
        onAccepted: webController.inspectRebind(selectedFile)
    }
    FileDialog {
        id: batchSourceDialog
        title: qsTr("选择批量记录")
        nameFilters: [qsTr("批量数据 (*.json *.csv)")]
        onAccepted: webController.createBatchVariantsFromFile(
            selectedFile, batchBindings.text, batchNameTemplate.text)
    }
    FileDialog {
        id: webExportDialog
        title: qsTr("导出网页片段")
        fileMode: FileDialog.SaveFile
        onAccepted: webController.exportSelected(
            selectedFile, String(exportFormat.currentValue),
            webController.timeMsForFrame(root.playheadFrame), exportBackground.text, true)
    }

    ColumnLayout {
        id: content
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.margins: 11
        spacing: 8

        Text {
            text: webController.componentData.name
                ? qsTr("网页组件 · ") + webController.componentData.name
                : qsTr("网页图层")
            color: Theme.text
            font.pixelSize: Theme.fontSizeBodySmall
            font.weight: Font.DemiBold
        }

        AppComboBox {
            id: layoutSelector
            Layout.fillWidth: true
            textRole: "name"
            valueRole: "id"
            model: webController.layoutOptions
            currentIndex: Math.max(0, indexOfValue(
                webController.persistentStateJson.length > 0
                    ? JSON.parse(webController.persistentStateJson).layout_id || "" : ""))
            onActivated: webController.selectLayout(String(currentValue || ""))
        }

        RowLayout {
            Layout.fillWidth: true
            AppButton {
                Layout.fillWidth: true
                primary: webController.editMode
                text: qsTr("网页编辑")
                onClicked: webController.setEditMode(true)
            }
            AppButton {
                Layout.fillWidth: true
                primary: !webController.editMode
                text: qsTr("合成预览")
                onClicked: webController.setEditMode(false)
            }
        }

        ListView {
            Layout.fillWidth: true
            Layout.preferredHeight: Math.min(210, Math.max(44, contentHeight))
            clip: true
            spacing: 4
            model: webController.layersModel
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
                color: webController.selectedLayerId === layerId ? Theme.accentSoft : Theme.surfaceRaised
                border.color: webController.selectedLayerId === layerId ? Theme.accent : Theme.border
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
                    Text {
                        text: keyframeCount > 0 ? kind + " · ◆" + keyframeCount : kind
                        color: Theme.textMuted
                        font.pixelSize: 10
                    }
                    AppButton {
                        implicitWidth: 30
                        text: layerVisible ? "◉" : "○"
                        onClicked: webController.updateLayer(layerId, {visible: !layerVisible})
                    }
                    AppButton {
                        implicitWidth: 30
                        text: allFieldsLocked ? "锁" : "开"
                        onClicked: webController.setLayerLocked(layerId, !allFieldsLocked)
                    }
                }
                TapHandler {
                    onTapped: webController.selectLayer(parent.layerId)
                }
            }
        }

        Text {
            Layout.fillWidth: true
            text: webController.selectedLayerData.name || qsTr("选择一个图层")
            color: Theme.text
            font.pixelSize: Theme.fontSizeCaption
            font.weight: Font.DemiBold
        }

        AppTextField {
            id: layerContent
            Layout.fillWidth: true
            visible: root.editable("content")
            text: String(webController.selectedLayerData.content ?? "")
            placeholderText: qsTr("文字内容")
        }

        AppTextField {
            id: layerColor
            Layout.fillWidth: true
            visible: root.editable("color")
            text: String(webController.selectedLayerData.color ?? "")
            placeholderText: qsTr("颜色，例如 #315efb")
        }

        AppTextField {
            id: layerFontFamily
            Layout.fillWidth: true
            visible: root.editable("font_family")
            text: String(webController.selectedLayerData.fontFamily ?? "")
            placeholderText: qsTr("字体")
        }

        AppTextField {
            id: layerImage
            Layout.fillWidth: true
            visible: root.editable("image")
            text: String(webController.selectedLayerData.image ?? "")
            placeholderText: qsTr("清单中的本地图片路径")
        }

        GridLayout {
            Layout.fillWidth: true
            columns: 2
            columnSpacing: 7
            rowSpacing: 6
            PropertyField { id: layerX; Layout.fillWidth: true; visible: root.editable("x"); label: "X"; text: String(webController.selectedLayerData.x ?? 0) }
            PropertyField { id: layerY; Layout.fillWidth: true; visible: root.editable("y"); label: "Y"; text: String(webController.selectedLayerData.y ?? 0) }
            PropertyField { id: layerFontSize; Layout.fillWidth: true; visible: root.editable("font_size"); label: qsTr("字号"); text: String(webController.selectedLayerData.fontSize ?? 16) }
            PropertyField { id: layerWidth; Layout.fillWidth: true; visible: root.editable("width"); label: qsTr("宽"); text: String(webController.selectedLayerData.width ?? 1) }
            PropertyField { id: layerHeight; Layout.fillWidth: true; visible: root.editable("height"); label: qsTr("高"); text: String(webController.selectedLayerData.height ?? 1) }
            PropertyField { id: layerRotation; Layout.fillWidth: true; visible: root.editable("rotation"); label: qsTr("旋转"); text: String(webController.selectedLayerData.rotation ?? 0) }
            PropertyField { id: layerOpacity; Layout.fillWidth: true; visible: root.editable("opacity"); label: qsTr("透明度"); text: String(webController.selectedLayerData.opacity ?? 1) }
            PropertyField { id: layerZ; Layout.fillWidth: true; visible: root.editable("z_index"); label: qsTr("层级"); text: String(webController.selectedLayerData.zIndex ?? 0) }
            PropertyField { id: enterMs; Layout.fillWidth: true; visible: root.editable("enter_ms"); label: qsTr("入场 ms"); text: String(webController.selectedLayerData.enterMs ?? 0) }
            PropertyField { id: exitMs; Layout.fillWidth: true; visible: root.editable("exit_ms"); label: qsTr("离场 ms"); text: String(webController.selectedLayerData.exitMs ?? 0) }
            PropertyField { id: delayMs; Layout.fillWidth: true; visible: root.editable("delay_ms"); label: qsTr("延迟 ms"); text: String(webController.selectedLayerData.delayMs ?? 0) }
            PropertyField { id: durationMs; Layout.fillWidth: true; visible: root.editable("duration_ms"); label: qsTr("动画 ms"); text: String(webController.selectedLayerData.durationMs ?? 0) }
        }

        Text {
            Layout.fillWidth: true
            visible: webController.selectedLayerId.length > 0
            text: qsTr("AI 可改字段（关闭后保留人工调整）")
            color: Theme.textMuted
            font.pixelSize: Theme.fontSizeCaption
        }

        GridLayout {
            Layout.fillWidth: true
            columns: 2
            columnSpacing: 6
            rowSpacing: 4
            Repeater {
                model: webController.selectedLayerData.editable || []
                AppCheckBox {
                    required property var modelData
                    Layout.fillWidth: true
                    text: String(modelData)
                    checked: (webController.selectedLayerData.lockedFields || []).indexOf(String(modelData)) < 0
                    onClicked: webController.setFieldLocked(
                        webController.selectedLayerId, String(modelData), !checked)
                }
            }
        }

        Text {
            Layout.fillWidth: true
            visible: webController.selectedLayerId.length > 0
            text: qsTr("关键帧 · 当前播放头 %1 帧").arg(root.playheadFrame)
            color: Theme.text
            font.pixelSize: Theme.fontSizeCaption
            font.weight: Font.DemiBold
        }

        RowLayout {
            Layout.fillWidth: true
            AppComboBox {
                id: keyframeField
                Layout.fillWidth: true
                model: webController.selectedLayerData.editable || []
            }
            AppComboBox {
                id: keyframeEasing
                Layout.fillWidth: true
                model: ["linear", "ease_in", "ease_out", "ease_in_out", "step", "cubic_bezier"]
            }
        }
        AppTextField {
            id: keyframeValue
            Layout.fillWidth: true
            text: String(root.fieldValue(String(keyframeField.currentValue || "")))
            placeholderText: qsTr("当前播放头处的值")
        }
        RowLayout {
            Layout.fillWidth: true
            AppButton {
                Layout.fillWidth: true
                text: qsTr("添加/更新关键帧")
                enabled: String(keyframeField.currentValue || "").length > 0
                onClicked: webController.setKeyframeAtFrame(
                    String(keyframeField.currentValue), keyframeValue.text,
                    String(keyframeEasing.currentValue), root.playheadFrame)
            }
            AppButton {
                Layout.fillWidth: true
                text: qsTr("移除关键帧")
                enabled: String(keyframeField.currentValue || "").length > 0
                onClicked: webController.removeKeyframeAtFrame(
                    String(keyframeField.currentValue), root.playheadFrame)
            }
        }

        AppButton {
            Layout.fillWidth: true
            primary: true
            enabled: webController.selectedLayerId.length > 0
            text: qsTr("应用图层参数")
            onClicked: {
                const changes = {};
                if (root.editable("content")) changes.content = layerContent.text;
                if (root.editable("color")) changes.color = layerColor.text;
                if (root.editable("font_family")) changes.font_family = layerFontFamily.text;
                if (root.editable("font_size")) changes.font_size = Number(layerFontSize.text);
                if (root.editable("image")) changes.image = layerImage.text;
                if (root.editable("x")) changes.x = Number(layerX.text);
                if (root.editable("y")) changes.y = Number(layerY.text);
                if (root.editable("width")) changes.width = Number(layerWidth.text);
                if (root.editable("height")) changes.height = Number(layerHeight.text);
                if (root.editable("rotation")) changes.rotation = Number(layerRotation.text);
                if (root.editable("opacity")) changes.opacity = Number(layerOpacity.text);
                if (root.editable("z_index")) changes.z_index = Number(layerZ.text);
                if (root.editable("enter_ms")) changes.enter_ms = Number(enterMs.text);
                if (root.editable("exit_ms")) changes.exit_ms = Number(exitMs.text);
                if (root.editable("delay_ms")) changes.delay_ms = Number(delayMs.text);
                if (root.editable("duration_ms")) changes.duration_ms = Number(durationMs.text);
                webController.updateLayer(webController.selectedLayerId, changes);
            }
        }

        Text {
            Layout.fillWidth: true
            visible: webController.themeOptions.length > 0
            text: qsTr("品牌主题")
            color: Theme.text
            font.pixelSize: Theme.fontSizeCaption
            font.weight: Font.DemiBold
        }
        Repeater {
            model: webController.themeOptions
            RowLayout {
                required property var modelData
                Layout.fillWidth: true
                Text {
                    Layout.preferredWidth: 100
                    text: modelData.name
                    color: Theme.textMuted
                    font.pixelSize: Theme.fontSizeCaption
                    elide: Text.ElideRight
                }
                AppTextField {
                    Layout.fillWidth: true
                    text: String(modelData.value)
                    onEditingFinished: webController.updateThemeValue(modelData.id, text)
                }
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
            placeholderText: qsTr("绑定 JSON，例如 {\"name\":\"layers.title.content\"}")
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
            onClicked: webController.createBatchVariants(
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
        Text {
            Layout.fillWidth: true
            visible: String(webController.rebindReport.new_source_hash || "").length > 0
            text: qsTr("新增 %1 · 保留 %2 · 移除 %3 · 冲突 %4")
                .arg((webController.rebindReport.added_layers || []).length)
                .arg((webController.rebindReport.retained_layers || []).length)
                .arg((webController.rebindReport.removed_layers || []).length)
                .arg((webController.rebindReport.conflicts || []).length)
            color: (webController.rebindReport.conflicts || []).length > 0
                ? Theme.warning : Theme.textMuted
            font.pixelSize: Theme.fontSizeCaption
            wrapMode: Text.WordWrap
        }
        RowLayout {
            Layout.fillWidth: true
            visible: String(webController.rebindReport.new_source_hash || "").length > 0
            AppButton {
                Layout.fillWidth: true
                text: qsTr("安全重新绑定")
                enabled: (webController.rebindReport.conflicts || []).length === 0
                onClicked: webController.commitRebind(false)
            }
            AppButton {
                Layout.fillWidth: true
                text: qsTr("接受冲突并迁移")
                enabled: (webController.rebindReport.conflicts || []).length > 0
                onClicked: webController.commitRebind(true)
            }
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
                model: ["png", "gif", "alpha_video", "video", "overlay"]
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
            visible: webController.dataOptions.length > 0
            text: qsTr("数据与图表")
            color: Theme.text
            font.pixelSize: Theme.fontSizeCaption
            font.weight: Font.DemiBold
        }
        Repeater {
            model: webController.dataOptions
            ColumnLayout {
                required property var modelData
                Layout.fillWidth: true
                Text {
                    text: modelData.name + " · " + modelData.kind
                    color: Theme.textMuted
                    font.pixelSize: Theme.fontSizeCaption
                }
                RowLayout {
                    Layout.fillWidth: true
                    AppTextField {
                        Layout.fillWidth: true
                        text: modelData.valueText
                        onEditingFinished: webController.updateDataValue(modelData.id, text)
                    }
                    AppButton {
                        text: qsTr("文件")
                        onClicked: {
                            root.snapshotFieldId = modelData.id;
                            snapshotDialog.open();
                        }
                    }
                }
            }
        }
    }
}

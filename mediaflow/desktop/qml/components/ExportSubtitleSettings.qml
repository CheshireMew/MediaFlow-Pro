import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import ".."

ColumnLayout {
    id: root

    property bool videoEnabled: true
    property var builtInPresets: settingsController.builtInSubtitleStylePresets
    property var presetOptions: {
        const values = root.builtInPresets.slice()
        const custom = settingsController.settingsData.subtitleStylePresets || []
        for (var index = 0; index < custom.length; ++index) {
            values.push({
                id: String(custom[index].id),
                name: String(custom[index].name),
                custom: true,
                style: custom[index].style
            })
        }
        return values
    }

    Layout.fillWidth: true
    visible: videoEnabled
    spacing: 8

    function currentStyle() {
        return {
            font_family: subtitleFont.text.trim(),
            font_size: Number(subtitleFontSize.value),
            font_color: subtitleColor.text.trim(),
            bold: subtitleBold.checked,
            italic: subtitleItalic.checked,
            outline_size: Number(subtitleOutlineSize.value),
            shadow_size: Number(subtitleShadowSize.value),
            outline_color: subtitleOutlineColor.text.trim(),
            background_enabled: subtitleBackground.checked,
            background_color: subtitleBackgroundColor.text.trim(),
            background_opacity: Number(subtitleBackgroundOpacity.value) / 100,
            background_padding: Number(subtitleBackgroundPadding.value),
            position_x: Number(subtitlePositionX.value) / 100,
            position_y: Number(subtitlePositionY.value) / 100,
            alignment: String(subtitleAlignment.currentValue),
            multiline_alignment: String(subtitleMultilineAlignment.currentValue)
        }
    }

    function exportOptions() {
        return {
            burnSubtitleTrackId: String(burnSubtitle.currentValue),
            subtitleStyle: root.currentStyle()
        }
    }

    function fontAvailable(fontFamily) {
        for (let index = 0; index < exportController.subtitleFontOptions.length; ++index) {
            const option = exportController.subtitleFontOptions[index]
            if (option.value === fontFamily)
                return option.available
        }
        return false
    }

    function applyStyle(style) {
        subtitleFont.text = style.font_family || "Microsoft YaHei UI"
        subtitleFontSize.value = Number(style.font_size ?? 24)
        subtitleColor.text = style.font_color || "#FFFFFF"
        subtitleBold.checked = Boolean(style.bold ?? false)
        subtitleItalic.checked = Boolean(style.italic ?? false)
        subtitleOutlineSize.value = Number(style.outline_size ?? 2)
        subtitleShadowSize.value = Number(style.shadow_size ?? 0)
        subtitleOutlineColor.text = style.outline_color || "#000000"
        subtitleBackground.checked = Boolean(style.background_enabled ?? false)
        subtitleBackgroundColor.text = style.background_color || "#000000"
        subtitleBackgroundOpacity.value = Math.round(Number(style.background_opacity ?? 0.5) * 100)
        subtitleBackgroundPadding.value = Number(style.background_padding ?? 5)
    }

    function restore(value) {
        const subtitleIndex = burnSubtitle.indexOfValue(value.burn_subtitle_track_id || "")
        if (subtitleIndex >= 0)
            burnSubtitle.currentIndex = subtitleIndex
        const style = value.subtitle_style || {}
        root.applyStyle(style)
        subtitleBold.checked = Boolean(style.bold ?? true)
        subtitleBackgroundOpacity.value = Math.round(Number(style.background_opacity ?? 0) * 100)
        subtitlePositionX.value = Math.round(Number(style.position_x ?? 0.5) * 100)
        subtitlePositionY.value = Math.round(Number(style.position_y ?? 0.88) * 100)
        const alignmentIndex = subtitleAlignment.indexOfValue(style.alignment || "center")
        if (alignmentIndex >= 0)
            subtitleAlignment.currentIndex = alignmentIndex
        const multilineIndex = subtitleMultilineAlignment.indexOfValue(
            style.multiline_alignment || "center")
        if (multilineIndex >= 0)
            subtitleMultilineAlignment.currentIndex = multilineIndex
    }

    Text {
        text: qsTr("烧录字幕（其余启用轨道仍导出 SRT）")
        color: Theme.textMuted
        font.pixelSize: Theme.fontSizeCaption
    }
    AppComboBox {
        id: burnSubtitle
        objectName: "exportBurnSubtitleTrack"
        Layout.fillWidth: true
        model: exportController.subtitleTrackOptions
        textRole: "label"
        valueRole: "value"
    }
    Text {
        text: qsTr("字幕样式")
        color: Theme.text
        font.pixelSize: Theme.fontSizeBody
        font.weight: Font.DemiBold
    }
    AppComboBox {
        id: subtitleStylePreset
        Layout.fillWidth: true
        model: root.presetOptions
        textRole: "name"
        valueRole: "id"
        onActivated: root.applyStyle(model[currentIndex].style)
    }
    RowLayout {
        Layout.fillWidth: true
        AppTextField {
            id: subtitleStylePresetName
            Layout.fillWidth: true
            placeholderText: qsTr("新样式预设名称")
        }
        AppButton {
            text: qsTr("保存样式")
            enabled: subtitleStylePresetName.text.trim().length > 0
            onClicked: {
                settingsController.saveSubtitleStylePreset(
                    subtitleStylePresetName.text,
                    JSON.stringify(root.currentStyle()))
                subtitleStylePresetName.clear()
            }
        }
        AppButton {
            text: qsTr("移除样式")
            danger: true
            visible: subtitleStylePreset.currentIndex >= 0
                && root.presetOptions[subtitleStylePreset.currentIndex].custom
            onClicked: settingsController.removeSubtitleStylePreset(
                String(subtitleStylePreset.currentValue))
        }
    }
    RowLayout {
        Layout.fillWidth: true
        AppTextField {
            id: subtitleFont
            objectName: "exportSubtitleFont"
            Layout.fillWidth: true
            text: "Microsoft YaHei UI"
            placeholderText: qsTr("字体名称")
        }
        AppComboBox {
            Layout.preferredWidth: 150
            textRole: "label"
            valueRole: "value"
            model: exportController.subtitleFontOptions
            onActivated: subtitleFont.text = String(currentValue)
        }
    }
    Text {
        Layout.fillWidth: true
        visible: subtitleFont.text.trim().length > 0
            && !root.fontAvailable(subtitleFont.text.trim())
        text: qsTr("字体“%1”当前不可用，导出时可能使用替代字体。").arg(subtitleFont.text.trim())
        color: Theme.warning
        font.pixelSize: Theme.fontSizeCaption
        wrapMode: Text.WordWrap
    }
    RowLayout {
        Layout.fillWidth: true
        Text { text: qsTr("字号"); color: Theme.textMuted; font.pixelSize: Theme.fontSizeCaption }
        AppSpinBox { id: subtitleFontSize; objectName: "exportSubtitleFontSize"; Layout.fillWidth: true; from: 8; to: 240; value: 24; editable: true }
        Text { text: qsTr("描边"); color: Theme.textMuted; font.pixelSize: Theme.fontSizeCaption }
        AppSpinBox { id: subtitleOutlineSize; Layout.fillWidth: true; from: 0; to: 30; value: 2; editable: true }
        Text { text: qsTr("阴影"); color: Theme.textMuted; font.pixelSize: Theme.fontSizeCaption }
        AppSpinBox { id: subtitleShadowSize; Layout.fillWidth: true; from: 0; to: 30; value: 0; editable: true }
    }
    RowLayout {
        Layout.fillWidth: true
        AppTextField { id: subtitleColor; objectName: "exportSubtitleColor"; Layout.fillWidth: true; text: "#FFFFFF"; placeholderText: qsTr("文字颜色 #RRGGBB") }
        AppTextField { id: subtitleOutlineColor; Layout.fillWidth: true; text: "#000000"; placeholderText: qsTr("描边颜色 #RRGGBB") }
    }
    RowLayout {
        Layout.fillWidth: true
        AppCheckBox { id: subtitleBold; text: qsTr("粗体"); checked: true }
        AppCheckBox { id: subtitleItalic; text: qsTr("斜体") }
        AppCheckBox { id: subtitleBackground; text: qsTr("字幕底色") }
    }
    RowLayout {
        Layout.fillWidth: true
        visible: subtitleBackground.checked
        AppTextField { id: subtitleBackgroundColor; Layout.fillWidth: true; text: "#000000"; placeholderText: qsTr("底色 #RRGGBB") }
        Text { text: qsTr("透明度"); color: Theme.textMuted; font.pixelSize: Theme.fontSizeCaption }
        AppSpinBox { id: subtitleBackgroundOpacity; Layout.fillWidth: true; from: 0; to: 100; value: 0; editable: true }
        Text { text: qsTr("内边距"); color: Theme.textMuted; font.pixelSize: Theme.fontSizeCaption }
        AppSpinBox { id: subtitleBackgroundPadding; Layout.fillWidth: true; from: 0; to: 100; value: 5; editable: true }
    }
    RowLayout {
        Layout.fillWidth: true
        Text { text: "X%"; color: Theme.textMuted; font.pixelSize: Theme.fontSizeCaption }
        AppSpinBox { id: subtitlePositionX; Layout.fillWidth: true; from: 0; to: 100; value: 50; editable: true }
        Text { text: "Y%"; color: Theme.textMuted; font.pixelSize: Theme.fontSizeCaption }
        AppSpinBox { id: subtitlePositionY; Layout.fillWidth: true; from: 0; to: 100; value: 88; editable: true }
        AppComboBox {
            id: subtitleAlignment
            Layout.fillWidth: true
            textRole: "label"
            valueRole: "value"
            model: [
                {label: qsTr("左对齐"), value: "left"},
                {label: qsTr("居中"), value: "center"},
                {label: qsTr("右对齐"), value: "right"}
            ]
        }
        AppComboBox {
            id: subtitleMultilineAlignment
            Layout.fillWidth: true
            textRole: "label"
            valueRole: "value"
            model: [
                {label: qsTr("多行顶部对齐"), value: "top"},
                {label: qsTr("多行居中对齐"), value: "center"},
                {label: qsTr("多行底部对齐"), value: "bottom"}
            ]
            currentIndex: 1
        }
    }
}

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Dialogs
import "."
import "components"

ColumnLayout {
    id: root
    spacing: 10
    property bool advancedOpen: false
    property var builtInSubtitleStylePresets: settingsController.builtInSubtitleStylePresets
    property var subtitleStylePresetOptions: {
        const values = root.builtInSubtitleStylePresets.slice()
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
    property var formats: exportController.exportFormatOptions

    function selectedFormat() { return root.formats[format.currentIndex] }

    function currentSubtitleStyle() {
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

    function subtitleFontAvailable(fontFamily) {
        for (let index = 0; index < exportController.subtitleFontOptions.length; ++index) {
            const option = exportController.subtitleFontOptions[index]
            if (option.value === fontFamily)
                return option.available
        }
        return false
    }

    function applySubtitleStyle(style) {
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

    function encoderModel() {
        const target = selectedFormat().value
        return exportController.videoEncoderOptions.filter(function(item) {
            return item.formats.indexOf(target) >= 0
        })
    }

    function restorePreset() {
        const value = exportController.exportPresetData
        if (!value || !value.format)
            return
        const advanced = value.advanced || {}
        for (var index = 0; index < root.formats.length; ++index) {
            const candidate = root.formats[index]
            if (candidate.value !== value.format)
                continue
            if (candidate.value === "audio" && candidate.container !== value.container)
                continue
            if (candidate.value === "prores"
                    && Number(candidate.profile) !== Number(advanced.profile ?? 3))
                continue
            format.currentIndex = index
            break
        }
        Qt.callLater(function() {
            const encoderIndex = encoderField.indexOfValue(value.video_codec || "")
            if (encoderIndex >= 0) encoderField.currentIndex = encoderIndex
            pixelFormatField.text = value.pixel_format || root.selectedFormat().pixelFormat
            qualityValue.value = Number(value.quality_value ?? qualityValue.value)
            presetField.text = value.preset || presetField.text
            gopFrames.value = Number(value.gop_frames ?? gopFrames.value)
            audioCodecField.text = value.audio_codec || "aac"
            audioBitrate.value = Math.round(Number(value.audio_bitrate ?? 192000) / 1000)
            widthField.text = String(advanced.width ?? workspaceController.profileWidth)
            heightField.text = String(advanced.height ?? workspaceController.profileHeight)
            fpsNumerator.text = String(advanced.fps_numerator ?? workspaceController.profileFpsNumerator)
            fpsDenominator.text = String(advanced.fps_denominator ?? workspaceController.profileFpsDenominator)
            targetBitrate.text = String(Math.round(Number(advanced.target_bitrate ?? 0) / 1000))
            maxBitrate.text = String(Math.round(Number(advanced.max_bitrate ?? 0) / 1000))
            const channelIndex = audioChannels.indexOfValue(
                Number(advanced.audio_channels ?? workspaceController.profileAudioChannels))
            if (channelIndex >= 0) audioChannels.currentIndex = channelIndex
            profileField.text = value.format === "prores" ? "" : String(advanced.profile ?? "")
            levelField.text = String(advanced.level ?? "")
            const scalingIndex = scalingMethod.indexOfValue(advanced.scaling_method || "lanczos")
            if (scalingIndex >= 0) scalingMethod.currentIndex = scalingIndex
            maxCll.text = String(advanced.max_cll ?? "1000,400")
            masterDisplay.text = String(advanced.master_display
                ?? "G(13250,34500)B(7500,3000)R(34000,16000)WP(15635,16450)L(10000000,1)")
            const subtitleIndex = burnSubtitle.indexOfValue(value.burn_subtitle_track_id || "")
            if (subtitleIndex >= 0) burnSubtitle.currentIndex = subtitleIndex
            const style = value.subtitle_style || {}
            subtitleFont.text = style.font_family || "Microsoft YaHei UI"
            subtitleFontSize.value = Number(style.font_size ?? 24)
            subtitleColor.text = style.font_color || "#FFFFFF"
            subtitleBold.checked = Boolean(style.bold ?? true)
            subtitleItalic.checked = Boolean(style.italic ?? false)
            subtitleOutlineSize.value = Number(style.outline_size ?? 2)
            subtitleShadowSize.value = Number(style.shadow_size ?? 0)
            subtitleOutlineColor.text = style.outline_color || "#000000"
            subtitleBackground.checked = Boolean(style.background_enabled ?? false)
            subtitleBackgroundColor.text = style.background_color || "#000000"
            subtitleBackgroundOpacity.value = Math.round(Number(style.background_opacity ?? 0) * 100)
            subtitleBackgroundPadding.value = Number(style.background_padding ?? 5)
            subtitlePositionX.value = Math.round(Number(style.position_x ?? 0.5) * 100)
            subtitlePositionY.value = Math.round(Number(style.position_y ?? 0.88) * 100)
            const subtitleAlignmentIndex = subtitleAlignment.indexOfValue(style.alignment || "center")
            if (subtitleAlignmentIndex >= 0) subtitleAlignment.currentIndex = subtitleAlignmentIndex
            const multilineIndex = subtitleMultilineAlignment.indexOfValue(
                style.multiline_alignment || "center")
            if (multilineIndex >= 0) subtitleMultilineAlignment.currentIndex = multilineIndex
            const watermark = value.watermark || {}
            watermarkEnabled.checked = Boolean(watermark.enabled ?? false)
            const watermarkIndex = watermarkAsset.indexOfValue(
                watermark.asset_id || mediaController.selectedWatermarkAssetId || "")
            if (watermarkIndex >= 0) watermarkAsset.currentIndex = watermarkIndex
            const watermarkPositionIndex = watermarkPosition.indexOfValue(watermark.position || "TR")
            if (watermarkPositionIndex >= 0) watermarkPosition.currentIndex = watermarkPositionIndex
            customWatermarkPosition.checked = watermark.position_x !== null
                && watermark.position_x !== undefined
                && watermark.position_y !== null
                && watermark.position_y !== undefined
            watermarkPositionX.value = Math.round(Number(watermark.position_x ?? 0.5) * 100)
            watermarkPositionY.value = Math.round(Number(watermark.position_y ?? 0.5) * 100)
            watermarkWidth.value = Math.round(Number(watermark.width_ratio ?? 0.2) * 100)
            watermarkOpacity.value = Math.round(Number(watermark.opacity ?? 1) * 100)
        })
    }

    Component.onCompleted: Qt.callLater(root.restorePreset)
    Connections {
        target: exportController
        function onProjectStateChanged() { Qt.callLater(root.restorePreset) }
    }

    function selectedWatermarkAssetId() {
        return watermarkAsset.currentIndex >= 0 && watermarkAsset.currentValue
            ? String(watermarkAsset.currentValue) : ""
    }

    FileDialog {
        id: watermarkDialog
        title: qsTr("选择水印图片")
        fileMode: FileDialog.OpenFile
        nameFilters: [qsTr("图片 (*.png *.jpg *.jpeg *.webp *.bmp *.tif *.tiff)")]
        onAccepted: mediaController.importWatermark(selectedFile.toString())
    }

    FileDialog {
        id: saveDialog
        title: qsTr("导出序列")
        fileMode: FileDialog.SaveFile
        defaultSuffix: root.formats[format.currentIndex].suffix
        nameFilters: [root.formats[format.currentIndex].filter]
        onAccepted: {
            var advanced = {
                width: Number(widthField.text),
                height: Number(heightField.text),
                fps_numerator: Number(fpsNumerator.text),
                fps_denominator: Number(fpsDenominator.text),
                audio_sample_rate: 48000,
                audio_channels: Number(audioChannels.currentValue),
                target_bitrate: Number(targetBitrate.text) * 1000,
                max_bitrate: Number(maxBitrate.text) * 1000,
                scaling_method: String(scalingMethod.currentValue)
            }
            if (root.selectedFormat().profile !== undefined)
                advanced.profile = Number(root.selectedFormat().profile)
            else if (profileField.text.trim().length > 0)
                advanced.profile = profileField.text.trim()
            if (levelField.text.trim().length > 0)
                advanced.level = levelField.text.trim()
            if (workspaceController.colorMode === "hdr10_bt2020_pq") {
                advanced.max_cll = maxCll.text
                advanced.master_display = masterDisplay.text
            }
            exportController.exportSequenceWithOptions(
                root.selectedFormat().value,
                selectedFile.toString(),
                {
                    container: root.selectedFormat().container || root.selectedFormat().suffix,
                    videoCodec: root.selectedFormat().value === "audio" ? "" : String(encoderField.currentValue),
                    pixelFormat: root.selectedFormat().value === "audio" ? "" : pixelFormatField.text,
                    qualityValue: Number(qualityValue.value),
                    preset: presetField.text,
                    gopFrames: Number(gopFrames.value),
                    audioCodec: root.selectedFormat().value === "audio"
                        ? root.selectedFormat().audioCodec : audioCodecField.text,
                    audioBitrate: Number(audioBitrate.value) * 1000,
                    burnSubtitleTrackId: String(burnSubtitle.currentValue),
                    subtitleStyle: root.currentSubtitleStyle(),
                    watermark: {
                        enabled: watermarkEnabled.checked,
                        asset_id: root.selectedWatermarkAssetId() || null,
                        position: String(watermarkPosition.currentValue),
                        position_x: customWatermarkPosition.checked
                            ? Number(watermarkPositionX.value) / 100 : null,
                        position_y: customWatermarkPosition.checked
                            ? Number(watermarkPositionY.value) / 100 : null,
                        width_ratio: Number(watermarkWidth.value) / 100,
                        opacity: Number(watermarkOpacity.value) / 100
                    },
                    advanced: advanced
                }
            )
        }
    }

    Text { text: qsTr("导出"); color: Theme.text; font.pixelSize: Theme.fontSizeSection; font.weight: Font.DemiBold }
    Panel {
        Layout.fillWidth: true
        implicitHeight: 94
        ColumnLayout {
            anchors.fill: parent; anchors.margins: 11; spacing: 5
            Text { text: qsTr("当前序列"); color: Theme.textMuted; font.pixelSize: Theme.fontSizeCaption }
            Text { Layout.fillWidth: true; text: workspaceController.profileLabel; color: Theme.text; font.pixelSize: Theme.fontSizeBody; font.weight: Font.DemiBold }
            Text { text: workspaceController.colorMode === "hdr10_bt2020_pq" ? qsTr("HDR10 · BT.2020 · PQ") : qsTr("SDR · BT.709"); color: workspaceController.colorMode === "hdr10_bt2020_pq" ? Theme.warning : Theme.accentHover; font.pixelSize: Theme.fontSizeCaption }
        }
    }
    Text { text: qsTr("格式"); color: Theme.textMuted; font.pixelSize: Theme.fontSizeCaption }
    AppComboBox {
        id: format
        Layout.fillWidth: true
        model: root.formats
        textRole: "label"
        valueRole: "value"
    }
    ScrollView {
        id: settingsScroll
        Layout.fillWidth: true
        Layout.fillHeight: true
        clip: true
        ColumnLayout {
            width: settingsScroll.availableWidth
            spacing: 10

            Panel {
                Layout.fillWidth: true
                implicitHeight: recommendedSettings.implicitHeight + 24
                ColumnLayout {
                    id: recommendedSettings
                    anchors.fill: parent
                    anchors.margins: 12
                    spacing: 8
                    Text {
                        text: qsTr("推荐设置")
                        color: Theme.text
                        font.pixelSize: Theme.fontSizeBody
                        font.weight: Font.DemiBold
                    }
                    Text {
                        visible: root.selectedFormat().value !== "audio"
                        text: qsTr("编码器")
                        color: Theme.textMuted
                        font.pixelSize: Theme.fontSizeCaption
                    }
                    AppComboBox {
                        id: encoderField
                        Layout.fillWidth: true
                        visible: root.selectedFormat().value !== "audio"
                        model: root.encoderModel()
                        textRole: "label"
                        valueRole: "value"
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        visible: root.selectedFormat().value !== "audio"
                        Text { text: "CRF/CQ"; color: Theme.textMuted; font.pixelSize: Theme.fontSizeCaption }
                        AppSpinBox { id: qualityValue; Layout.fillWidth: true; from: 0; to: 63; value: Number(root.selectedFormat().qualityValue); editable: true }
                        Text { text: "GOP"; color: Theme.textMuted; font.pixelSize: Theme.fontSizeCaption }
                        AppSpinBox { id: gopFrames; Layout.fillWidth: true; from: 1; to: 600; value: Math.max(1, Math.round(workspaceController.profileFpsNumerator / workspaceController.profileFpsDenominator * 2)); editable: true }
                    }
                    Text {
                        Layout.fillWidth: true
                        visible: root.selectedFormat().value === "audio"
                        text: qsTr("编码器和封装会根据所选音频格式自动匹配。")
                        color: Theme.textMuted
                        font.pixelSize: Theme.fontSizeCaption
                        wrapMode: Text.WordWrap
                    }
                }
            }

            AppButton {
                id: advancedToggle
                objectName: "exportAdvancedToggle"
                Layout.fillWidth: true
                checkable: true
                checked: root.advancedOpen
                text: root.advancedOpen ? qsTr("收起高级设置") : qsTr("显示高级设置")
                onClicked: root.advancedOpen = !root.advancedOpen
            }

            Panel {
                id: advancedSection
                objectName: "exportAdvancedSection"
                Layout.fillWidth: true
                visible: root.advancedOpen
                level: 1
                implicitHeight: advancedSettings.implicitHeight + 24
                ColumnLayout {
                    id: advancedSettings
                    anchors.fill: parent
                    anchors.margins: 12
                    spacing: 8
                    Text { visible: root.selectedFormat().value !== "audio"; text: qsTr("像素格式"); color: Theme.textMuted; font.pixelSize: Theme.fontSizeCaption }
                    AppTextField { id: pixelFormatField; Layout.fillWidth: true; visible: root.selectedFormat().value !== "audio"; text: root.selectedFormat().pixelFormat }
                    Text { visible: root.selectedFormat().value !== "audio"; text: qsTr("编码预设"); color: Theme.textMuted; font.pixelSize: Theme.fontSizeCaption }
                    AppTextField { id: presetField; Layout.fillWidth: true; visible: root.selectedFormat().value !== "audio"; text: root.selectedFormat().preset }
                    RowLayout {
                        Layout.fillWidth: true
                        visible: root.selectedFormat().value !== "audio"
                        AppTextField { id: profileField; Layout.fillWidth: true; placeholderText: qsTr("Profile（可选）") }
                        AppTextField { id: levelField; Layout.fillWidth: true; placeholderText: qsTr("Level（可选）") }
                    }
                    AppComboBox {
                        id: scalingMethod
                        Layout.fillWidth: true
                        visible: root.selectedFormat().value !== "audio"
                        textRole: "label"; valueRole: "value"
                        model: [
                            {label: qsTr("缩放：Lanczos"), value: "lanczos"},
                            {label: qsTr("缩放：双三次"), value: "bicubic"},
                            {label: qsTr("缩放：双线性"), value: "bilinear"}
                        ]
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        visible: root.selectedFormat().value !== "audio"
                        AppTextField { id: widthField; Layout.fillWidth: true; text: String(workspaceController.profileWidth); placeholderText: qsTr("宽") }
                        Text { text: "×"; color: Theme.textMuted }
                        AppTextField { id: heightField; Layout.fillWidth: true; text: String(workspaceController.profileHeight); placeholderText: qsTr("高") }
                    }
                    Text { visible: root.selectedFormat().value !== "audio"; text: qsTr("烧录字幕（其余启用轨道仍导出 SRT）"); color: Theme.textMuted; font.pixelSize: Theme.fontSizeCaption }
                    AppComboBox {
                        id: burnSubtitle
                        Layout.fillWidth: true
                        visible: root.selectedFormat().value !== "audio"
                        model: exportController.subtitleTrackOptions
                        textRole: "label"; valueRole: "value"
                    }
                    Text { visible: root.selectedFormat().value !== "audio"; text: qsTr("字幕样式"); color: Theme.text; font.pixelSize: Theme.fontSizeBody; font.weight: Font.DemiBold }
                    AppComboBox {
                        id: subtitleStylePreset
                        Layout.fillWidth: true
                        visible: root.selectedFormat().value !== "audio"
                        model: root.subtitleStylePresetOptions
                        textRole: "name"; valueRole: "id"
                        onActivated: root.applySubtitleStyle(model[currentIndex].style)
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        visible: root.selectedFormat().value !== "audio"
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
                                    JSON.stringify(root.currentSubtitleStyle()))
                                subtitleStylePresetName.clear()
                            }
                        }
                        AppButton {
                            text: qsTr("移除样式")
                            danger: true
                            visible: subtitleStylePreset.currentIndex >= 0
                                && root.subtitleStylePresetOptions[subtitleStylePreset.currentIndex].custom
                            onClicked: settingsController.removeSubtitleStylePreset(
                                String(subtitleStylePreset.currentValue))
                        }
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        visible: root.selectedFormat().value !== "audio"
                        AppTextField { id: subtitleFont; Layout.fillWidth: true; text: "Microsoft YaHei UI"; placeholderText: qsTr("字体名称") }
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
                        visible: root.selectedFormat().value !== "audio"
                                 && subtitleFont.text.trim().length > 0
                                 && !root.subtitleFontAvailable(subtitleFont.text.trim())
                        text: qsTr("字体“%1”当前不可用，导出时可能使用替代字体。").arg(subtitleFont.text.trim())
                        color: Theme.warning
                        font.pixelSize: Theme.fontSizeCaption
                        wrapMode: Text.WordWrap
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        visible: root.selectedFormat().value !== "audio"
                        Text { text: qsTr("字号"); color: Theme.textMuted; font.pixelSize: Theme.fontSizeCaption }
                        AppSpinBox { id: subtitleFontSize; Layout.fillWidth: true; from: 8; to: 240; value: 24; editable: true }
                        Text { text: qsTr("描边"); color: Theme.textMuted; font.pixelSize: Theme.fontSizeCaption }
                        AppSpinBox { id: subtitleOutlineSize; Layout.fillWidth: true; from: 0; to: 30; value: 2; editable: true }
                        Text { text: qsTr("阴影"); color: Theme.textMuted; font.pixelSize: Theme.fontSizeCaption }
                        AppSpinBox { id: subtitleShadowSize; Layout.fillWidth: true; from: 0; to: 30; value: 0; editable: true }
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        visible: root.selectedFormat().value !== "audio"
                        AppTextField { id: subtitleColor; Layout.fillWidth: true; text: "#FFFFFF"; placeholderText: qsTr("文字颜色 #RRGGBB") }
                        AppTextField { id: subtitleOutlineColor; Layout.fillWidth: true; text: "#000000"; placeholderText: qsTr("描边颜色 #RRGGBB") }
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        visible: root.selectedFormat().value !== "audio"
                        AppCheckBox { id: subtitleBold; text: qsTr("粗体"); checked: true }
                        AppCheckBox { id: subtitleItalic; text: qsTr("斜体") }
                        AppCheckBox { id: subtitleBackground; text: qsTr("字幕底色") }
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        visible: root.selectedFormat().value !== "audio" && subtitleBackground.checked
                        AppTextField { id: subtitleBackgroundColor; Layout.fillWidth: true; text: "#000000"; placeholderText: qsTr("底色 #RRGGBB") }
                        Text { text: qsTr("透明度"); color: Theme.textMuted; font.pixelSize: Theme.fontSizeCaption }
                        AppSpinBox { id: subtitleBackgroundOpacity; Layout.fillWidth: true; from: 0; to: 100; value: 0; editable: true }
                        Text { text: qsTr("内边距"); color: Theme.textMuted; font.pixelSize: Theme.fontSizeCaption }
                        AppSpinBox { id: subtitleBackgroundPadding; Layout.fillWidth: true; from: 0; to: 100; value: 5; editable: true }
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        visible: root.selectedFormat().value !== "audio"
                        Text { text: "X%"; color: Theme.textMuted; font.pixelSize: Theme.fontSizeCaption }
                        AppSpinBox { id: subtitlePositionX; Layout.fillWidth: true; from: 0; to: 100; value: 50; editable: true }
                        Text { text: "Y%"; color: Theme.textMuted; font.pixelSize: Theme.fontSizeCaption }
                        AppSpinBox { id: subtitlePositionY; Layout.fillWidth: true; from: 0; to: 100; value: 88; editable: true }
                        AppComboBox {
                            id: subtitleAlignment
                            Layout.fillWidth: true
                            textRole: "label"; valueRole: "value"
                            model: [{label: qsTr("左对齐"), value: "left"}, {label: qsTr("居中"), value: "center"}, {label: qsTr("右对齐"), value: "right"}]
                        }
                        AppComboBox {
                            id: subtitleMultilineAlignment
                            Layout.fillWidth: true
                            textRole: "label"; valueRole: "value"
                            model: [
                                {label: qsTr("多行顶部对齐"), value: "top"},
                                {label: qsTr("多行居中对齐"), value: "center"},
                                {label: qsTr("多行底部对齐"), value: "bottom"}
                            ]
                            currentIndex: 1
                        }
                    }
                    Text { visible: root.selectedFormat().value !== "audio"; text: qsTr("水印"); color: Theme.text; font.pixelSize: Theme.fontSizeBody; font.weight: Font.DemiBold }
                    AppCheckBox { id: watermarkEnabled; visible: root.selectedFormat().value !== "audio"; text: qsTr("启用图片水印") }
                    RowLayout {
                        Layout.fillWidth: true
                        visible: root.selectedFormat().value !== "audio"
                        AppComboBox { id: watermarkAsset; Layout.fillWidth: true; model: mediaController.watermarkAssetOptions; textRole: "label"; valueRole: "value"; onActivated: mediaController.selectWatermarkAsset(String(currentValue)) }
                        AppButton { text: qsTr("导入图片"); onClicked: watermarkDialog.open() }
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        visible: root.selectedFormat().value !== "audio" && watermarkEnabled.checked
                        AppComboBox {
                            id: watermarkPosition
                            Layout.fillWidth: true
                            textRole: "label"; valueRole: "value"
                            model: [
                                {label: qsTr("左上"), value: "TL"}, {label: qsTr("上中"), value: "TC"}, {label: qsTr("右上"), value: "TR"},
                                {label: qsTr("左中"), value: "LC"}, {label: qsTr("居中"), value: "C"}, {label: qsTr("右中"), value: "RC"},
                                {label: qsTr("左下"), value: "BL"}, {label: qsTr("下中"), value: "BC"}, {label: qsTr("右下"), value: "BR"}
                            ]
                            currentIndex: 2
                        }
                        Text { text: qsTr("宽度 %"); color: Theme.textMuted; font.pixelSize: Theme.fontSizeCaption }
                        AppSpinBox { id: watermarkWidth; Layout.fillWidth: true; from: 1; to: 100; value: 20; editable: true }
                        Text { text: qsTr("不透明度 %"); color: Theme.textMuted; font.pixelSize: Theme.fontSizeCaption }
                        AppSpinBox { id: watermarkOpacity; Layout.fillWidth: true; from: 0; to: 100; value: 100; editable: true }
                    }
                    AppCheckBox {
                        id: customWatermarkPosition
                        visible: root.selectedFormat().value !== "audio" && watermarkEnabled.checked
                        text: qsTr("自定义水印中心位置")
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        visible: root.selectedFormat().value !== "audio"
                            && watermarkEnabled.checked && customWatermarkPosition.checked
                        Text { text: "X%"; color: Theme.textMuted; font.pixelSize: Theme.fontSizeCaption }
                        AppSpinBox { id: watermarkPositionX; Layout.fillWidth: true; from: 0; to: 100; value: 50; editable: true }
                        Text { text: "Y%"; color: Theme.textMuted; font.pixelSize: Theme.fontSizeCaption }
                        AppSpinBox { id: watermarkPositionY; Layout.fillWidth: true; from: 0; to: 100; value: 50; editable: true }
                    }
                    Text { text: qsTr("导出范围"); color: Theme.text; font.pixelSize: Theme.fontSizeBody; font.weight: Font.DemiBold }
                    Text {
                        Layout.fillWidth: true
                        text: workspaceController.hasSequenceInOut
                            ? qsTr("使用时间线入出点：%1–%2 帧").arg(
                                workspaceController.sequenceInFrame).arg(workspaceController.sequenceOutFrame)
                            : qsTr("未设置序列入出点，将导出完整时间线")
                        color: workspaceController.hasSequenceInOut ? Theme.accentHover : Theme.textMuted
                        font.pixelSize: Theme.fontSizeCaption
                        wrapMode: Text.WordWrap
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        visible: root.selectedFormat().value !== "audio"
                        AppTextField { id: fpsNumerator; Layout.fillWidth: true; text: String(workspaceController.profileFpsNumerator); placeholderText: "FPS num" }
                        Text { text: "/"; color: Theme.textMuted }
                        AppTextField { id: fpsDenominator; Layout.fillWidth: true; text: String(workspaceController.profileFpsDenominator); placeholderText: "FPS den" }
                    }
                    AppTextField { id: targetBitrate; Layout.fillWidth: true; visible: root.selectedFormat().value !== "audio"; text: "0"; placeholderText: qsTr("目标码率 kbps（0=质量模式）") }
                    AppTextField { id: maxBitrate; Layout.fillWidth: true; visible: root.selectedFormat().value !== "audio"; text: "0"; placeholderText: qsTr("最大码率 kbps（0=不限）") }
                    AppTextField { id: audioCodecField; Layout.fillWidth: true; visible: root.selectedFormat().value !== "audio"; text: "aac"; placeholderText: qsTr("音频编码器") }
                    RowLayout {
                        Layout.fillWidth: true
                        Text { text: qsTr("音频 kbps"); color: Theme.textMuted; font.pixelSize: Theme.fontSizeCaption }
                        AppSpinBox { id: audioBitrate; Layout.fillWidth: true; from: 64; to: 1536; value: 192; editable: true }
                        AppComboBox { id: audioChannels; Layout.fillWidth: true; textRole: "text"; valueRole: "value"; model: [{text: "Mono", value: 1}, {text: "Stereo", value: 2}, {text: "5.1", value: 6}]; currentIndex: 1 }
                    }
                    AppTextField { id: maxCll; Layout.fillWidth: true; visible: workspaceController.colorMode === "hdr10_bt2020_pq" && root.selectedFormat().value !== "audio"; text: "1000,400"; placeholderText: "MaxCLL,MaxFALL" }
                    AppTextField { id: masterDisplay; Layout.fillWidth: true; visible: workspaceController.colorMode === "hdr10_bt2020_pq" && root.selectedFormat().value !== "audio"; text: "G(13250,34500)B(7500,3000)R(34000,16000)WP(15635,16450)L(10000000,1)"; placeholderText: qsTr("母版显示元数据") }
                    Text {
                        Layout.fillWidth: true
                        text: root.selectedFormat().value === "audio"
                            ? qsTr("导出后会实际运行 ffprobe，校验封装、音频编码和声道。")
                            : qsTr("导出后会实际运行 ffprobe，校验分辨率、编码、位深、色彩和 HDR 母版元数据。")
                        color: Theme.textMuted
                        font.pixelSize: Theme.fontSizeCaption
                        wrapMode: Text.WordWrap
                    }
                }
            }
        }
    }
    AppButton { Layout.fillWidth: true; primary: true; text: qsTr("选择位置并导出"); onClicked: saveDialog.open() }
    Text { Layout.fillWidth: true; text: qsTr("导出使用原始素材和与预览相同的 MLT 时间线图。进度、失败原因及产物入口显示在任务抽屉。"); color: Theme.textMuted; font.pixelSize: Theme.fontSizeCaption; wrapMode: Text.WordWrap }
}

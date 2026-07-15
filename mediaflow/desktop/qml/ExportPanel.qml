import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Dialogs
import "."
import "components"

ColumnLayout {
    id: root
    spacing: 10
    property var formats: projectController.colorMode === "hdr10_bt2020_pq"
        ? [
            { label: "HEVC Main10", value: "hevc", suffix: "mp4", filter: qsTr("MP4 视频 (*.mp4)") },
            { label: "AV1 10-bit", value: "av1", suffix: "mkv", filter: qsTr("MKV 视频 (*.mkv)") },
            { label: "ProRes Proxy", value: "prores", suffix: "mov", filter: qsTr("MOV 视频 (*.mov)"), profile: 0 },
            { label: "ProRes LT", value: "prores", suffix: "mov", filter: qsTr("MOV 视频 (*.mov)"), profile: 1 },
            { label: "ProRes Standard", value: "prores", suffix: "mov", filter: qsTr("MOV 视频 (*.mov)"), profile: 2 },
            { label: "ProRes HQ", value: "prores", suffix: "mov", filter: qsTr("MOV 视频 (*.mov)"), profile: 3 },
            { label: "ProRes 4444", value: "prores", suffix: "mov", filter: qsTr("MOV 视频 (*.mov)"), profile: 4 },
            { label: "AAC / M4A", value: "audio", suffix: "m4a", container: "ipod", audioCodec: "aac", filter: qsTr("M4A 音频 (*.m4a)") },
            { label: "Opus / OGG", value: "audio", suffix: "ogg", container: "ogg", audioCodec: "libopus", filter: qsTr("OGG 音频 (*.ogg)") },
            { label: "PCM / WAV", value: "audio", suffix: "wav", container: "wav", audioCodec: "pcm_s24le", filter: qsTr("WAV 音频 (*.wav)") },
            { label: "FLAC", value: "audio", suffix: "flac", container: "flac", audioCodec: "flac", filter: qsTr("FLAC 音频 (*.flac)") }
          ]
        : [
            { label: "H.264", value: "h264", suffix: "mp4", filter: qsTr("MP4 视频 (*.mp4)") },
            { label: "HEVC", value: "hevc", suffix: "mp4", filter: qsTr("MP4 视频 (*.mp4)") },
            { label: "AV1", value: "av1", suffix: "mkv", filter: qsTr("MKV 视频 (*.mkv)") },
            { label: "ProRes Proxy", value: "prores", suffix: "mov", filter: qsTr("MOV 视频 (*.mov)"), profile: 0 },
            { label: "ProRes LT", value: "prores", suffix: "mov", filter: qsTr("MOV 视频 (*.mov)"), profile: 1 },
            { label: "ProRes Standard", value: "prores", suffix: "mov", filter: qsTr("MOV 视频 (*.mov)"), profile: 2 },
            { label: "ProRes HQ", value: "prores", suffix: "mov", filter: qsTr("MOV 视频 (*.mov)"), profile: 3 },
            { label: "ProRes 4444", value: "prores", suffix: "mov", filter: qsTr("MOV 视频 (*.mov)"), profile: 4 },
            { label: "AAC / M4A", value: "audio", suffix: "m4a", container: "ipod", audioCodec: "aac", filter: qsTr("M4A 音频 (*.m4a)") },
            { label: "Opus / OGG", value: "audio", suffix: "ogg", container: "ogg", audioCodec: "libopus", filter: qsTr("OGG 音频 (*.ogg)") },
            { label: "PCM / WAV", value: "audio", suffix: "wav", container: "wav", audioCodec: "pcm_s24le", filter: qsTr("WAV 音频 (*.wav)") },
            { label: "FLAC", value: "audio", suffix: "flac", container: "flac", audioCodec: "flac", filter: qsTr("FLAC 音频 (*.flac)") }
          ]

    function selectedFormat() { return root.formats[format.currentIndex] }

    function encoderModel() {
        const target = selectedFormat().value
        return projectController.videoEncoderOptions.filter(function(item) {
            return item.formats.indexOf(target) >= 0
        })
    }

    function restorePreset() {
        const value = projectController.exportPresetData
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
            pixelFormatField.text = value.pixel_format || root.defaultPixelFormat()
            qualityValue.value = Number(value.quality_value ?? qualityValue.value)
            presetField.text = value.preset || presetField.text
            gopFrames.value = Number(value.gop_frames ?? gopFrames.value)
            audioCodecField.text = value.audio_codec || "aac"
            audioBitrate.value = Math.round(Number(value.audio_bitrate ?? 192000) / 1000)
            widthField.text = String(advanced.width ?? projectController.profileWidth)
            heightField.text = String(advanced.height ?? projectController.profileHeight)
            fpsNumerator.text = String(advanced.fps_numerator ?? projectController.profileFpsNumerator)
            fpsDenominator.text = String(advanced.fps_denominator ?? projectController.profileFpsDenominator)
            targetBitrate.text = String(Math.round(Number(advanced.target_bitrate ?? 0) / 1000))
            maxBitrate.text = String(Math.round(Number(advanced.max_bitrate ?? 0) / 1000))
            const channelIndex = audioChannels.indexOfValue(
                Number(advanced.audio_channels ?? projectController.profileAudioChannels))
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
        })
    }

    Component.onCompleted: Qt.callLater(root.restorePreset)
    Connections {
        target: projectController
        function onProjectStateChanged() { Qt.callLater(root.restorePreset) }
    }

    function defaultVideoCodec() {
        var value = root.selectedFormat().value
        if (value === "h264") return "libx264"
        if (value === "hevc") return "libx265"
        if (value === "av1") return "libsvtav1"
        if (value === "prores") return "prores_ks"
        return ""
    }

    function defaultPixelFormat() {
        var value = root.selectedFormat().value
        if (value === "prores") return root.selectedFormat().profile === 4 ? "yuva444p10le" : "yuv422p10le"
        if (projectController.colorMode === "hdr10_bt2020_pq") return "yuv420p10le"
        return "yuv420p"
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
            if (projectController.colorMode === "hdr10_bt2020_pq") {
                advanced.max_cll = maxCll.text
                advanced.master_display = masterDisplay.text
            }
            projectController.exportSequenceWithOptions(
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
                    advanced: advanced
                }
            )
        }
    }

    Text { text: qsTr("导出"); color: Theme.text; font.pixelSize: 16; font.weight: Font.DemiBold }
    Panel {
        Layout.fillWidth: true
        implicitHeight: 94
        ColumnLayout {
            anchors.fill: parent; anchors.margins: 11; spacing: 5
            Text { text: qsTr("当前序列"); color: Theme.textMuted; font.pixelSize: 10 }
            Text { Layout.fillWidth: true; text: projectController.profileLabel; color: Theme.text; font.pixelSize: 13; font.weight: Font.DemiBold }
            Text { text: projectController.colorMode === "hdr10_bt2020_pq" ? qsTr("HDR10 · BT.2020 · PQ") : qsTr("SDR · BT.709"); color: projectController.colorMode === "hdr10_bt2020_pq" ? Theme.warning : Theme.accentHover; font.pixelSize: 10 }
        }
    }
    Text { text: qsTr("格式"); color: Theme.textMuted; font.pixelSize: 11 }
    ComboBox {
        id: format
        Layout.fillWidth: true
        model: root.formats
        textRole: "label"
        valueRole: "value"
    }
    ScrollView {
        Layout.fillWidth: true
        Layout.fillHeight: true
        clip: true
        ColumnLayout {
            width: parent.width
            spacing: 8
            Text { text: qsTr("编码器"); color: Theme.textMuted; font.pixelSize: 10 }
            ComboBox {
                id: encoderField
                Layout.fillWidth: true
                visible: root.selectedFormat().value !== "audio"
                model: root.encoderModel()
                textRole: "label"
                valueRole: "value"
            }
            Text { text: qsTr("像素格式"); color: Theme.textMuted; font.pixelSize: 10 }
            TextField { id: pixelFormatField; Layout.fillWidth: true; text: root.defaultPixelFormat(); color: Theme.text }
            RowLayout {
                Text { text: "CRF/CQ"; color: Theme.textMuted; font.pixelSize: 10 }
                SpinBox { id: qualityValue; from: 0; to: 63; value: root.formats[format.currentIndex].value === "av1" ? 24 : 18; editable: true }
                Text { text: "GOP"; color: Theme.textMuted; font.pixelSize: 10 }
                SpinBox { id: gopFrames; from: 1; to: 600; value: Math.max(1, Math.round(projectController.profileFpsNumerator / projectController.profileFpsDenominator * 2)); editable: true }
            }
            Text { text: qsTr("编码预设"); color: Theme.textMuted; font.pixelSize: 10 }
            TextField { id: presetField; Layout.fillWidth: true; text: root.formats[format.currentIndex].value === "av1" ? "8" : "medium"; color: Theme.text }
            RowLayout {
                Layout.fillWidth: true
                TextField { id: profileField; Layout.fillWidth: true; placeholderText: qsTr("Profile（可选）"); color: Theme.text }
                TextField { id: levelField; Layout.fillWidth: true; placeholderText: qsTr("Level（可选）"); color: Theme.text }
            }
            ComboBox {
                id: scalingMethod
                Layout.fillWidth: true
                textRole: "label"; valueRole: "value"
                model: [
                    {label: qsTr("缩放：Lanczos"), value: "lanczos"},
                    {label: qsTr("缩放：双三次"), value: "bicubic"},
                    {label: qsTr("缩放：双线性"), value: "bilinear"}
                ]
            }
            RowLayout {
                TextField { id: widthField; Layout.fillWidth: true; text: String(projectController.profileWidth); placeholderText: qsTr("宽") ; color: Theme.text }
                Text { text: "×"; color: Theme.textMuted }
                TextField { id: heightField; Layout.fillWidth: true; text: String(projectController.profileHeight); placeholderText: qsTr("高"); color: Theme.text }
            }
            Text { text: qsTr("烧录字幕（其余启用轨道仍导出 SRT）"); color: Theme.textMuted; font.pixelSize: 10 }
            ComboBox {
                id: burnSubtitle
                Layout.fillWidth: true
                model: projectController.subtitleTrackOptions
                textRole: "label"; valueRole: "value"
            }
            RowLayout {
                TextField { id: fpsNumerator; Layout.fillWidth: true; text: String(projectController.profileFpsNumerator); placeholderText: "FPS num"; color: Theme.text }
                Text { text: "/"; color: Theme.textMuted }
                TextField { id: fpsDenominator; Layout.fillWidth: true; text: String(projectController.profileFpsDenominator); placeholderText: "FPS den"; color: Theme.text }
            }
            TextField { id: targetBitrate; Layout.fillWidth: true; text: "0"; placeholderText: qsTr("目标码率 kbps（0=质量模式）"); color: Theme.text }
            TextField { id: maxBitrate; Layout.fillWidth: true; text: "0"; placeholderText: qsTr("最大码率 kbps（0=不限）"); color: Theme.text }
            TextField { id: audioCodecField; Layout.fillWidth: true; text: "aac"; placeholderText: qsTr("音频编码器"); color: Theme.text }
            RowLayout {
                Text { text: qsTr("音频 kbps"); color: Theme.textMuted; font.pixelSize: 10 }
                SpinBox { id: audioBitrate; from: 64; to: 1536; value: 192; editable: true }
                ComboBox { id: audioChannels; textRole: "text"; valueRole: "value"; model: [{text: "Mono", value: 1}, {text: "Stereo", value: 2}, {text: "5.1", value: 6}]; currentIndex: 1 }
            }
            TextField { id: maxCll; Layout.fillWidth: true; visible: projectController.colorMode === "hdr10_bt2020_pq"; text: "1000,400"; placeholderText: "MaxCLL,MaxFALL"; color: Theme.text }
            TextField { id: masterDisplay; Layout.fillWidth: true; visible: projectController.colorMode === "hdr10_bt2020_pq"; text: "G(13250,34500)B(7500,3000)R(34000,16000)WP(15635,16450)L(10000000,1)"; placeholderText: qsTr("母版显示元数据"); color: Theme.text }
            Text { Layout.fillWidth: true; text: qsTr("导出后会实际运行 ffprobe，校验分辨率、编码、位深、色彩和 HDR 母版元数据。"); color: Theme.textMuted; font.pixelSize: 10; wrapMode: Text.WordWrap }
        }
    }
    AppButton { Layout.fillWidth: true; primary: true; text: qsTr("选择位置并导出"); onClicked: saveDialog.open() }
    Text { Layout.fillWidth: true; text: qsTr("导出使用原始素材和与预览相同的 MLT 时间线图。进度、失败原因及产物入口显示在任务抽屉。"); color: Theme.textMuted; font.pixelSize: 10; wrapMode: Text.WordWrap }
}

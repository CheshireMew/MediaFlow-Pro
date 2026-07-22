import QtQuick
import QtQuick.Layouts
import ".."

ColumnLayout {
    id: root

    property var format

    readonly property bool videoEnabled: format && format.value !== "audio"

    Layout.fillWidth: true
    spacing: 8

    function restore(value) {
        const advanced = value.advanced || {}
        pixelFormatField.text = value.pixel_format || root.format.pixelFormat
        presetField.text = value.preset || presetField.text
        qualityValue.value = Number(value.quality_value ?? qualityValue.value)
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
        if (channelIndex >= 0)
            audioChannels.currentIndex = channelIndex
        profileField.text = value.format === "prores" ? "" : String(advanced.profile ?? "")
        levelField.text = String(advanced.level ?? "")
        const scalingIndex = scalingMethod.indexOfValue(advanced.scaling_method || "lanczos")
        if (scalingIndex >= 0)
            scalingMethod.currentIndex = scalingIndex
        maxCll.text = String(advanced.max_cll ?? "1000,400")
        masterDisplay.text = String(advanced.master_display
            ?? "G(13250,34500)B(7500,3000)R(34000,16000)WP(15635,16450)L(10000000,1)")
    }

    function exportOptions() {
        const advanced = {
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
        if (root.format.profile !== undefined)
            advanced.profile = Number(root.format.profile)
        else if (profileField.text.trim().length > 0)
            advanced.profile = profileField.text.trim()
        if (levelField.text.trim().length > 0)
            advanced.level = levelField.text.trim()
        if (workspaceController.colorMode === "hdr10_bt2020_pq") {
            advanced.max_cll = maxCll.text
            advanced.master_display = masterDisplay.text
        }
        return {
            pixelFormat: root.videoEnabled ? pixelFormatField.text : "",
            preset: presetField.text,
            qualityValue: Number(qualityValue.value),
            gopFrames: Number(gopFrames.value),
            audioCodec: root.videoEnabled ? audioCodecField.text : root.format.audioCodec,
            audioBitrate: Number(audioBitrate.value) * 1000,
            advanced: advanced
        }
    }

    Text {
        visible: root.videoEnabled
        text: qsTr("像素格式")
        color: Theme.textMuted
        font.pixelSize: Theme.fontSizeCaption
    }
    AppTextField {
        id: pixelFormatField
        Layout.fillWidth: true
        visible: root.videoEnabled
        text: root.format ? root.format.pixelFormat : ""
    }
    Text {
        visible: root.videoEnabled
        text: qsTr("编码预设")
        color: Theme.textMuted
        font.pixelSize: Theme.fontSizeCaption
    }
    AppTextField {
        id: presetField
        Layout.fillWidth: true
        visible: root.videoEnabled
        text: root.format ? root.format.preset : ""
    }
    RowLayout {
        Layout.fillWidth: true
        visible: root.videoEnabled
        spacing: 8
        ColumnLayout {
            Layout.fillWidth: true
            spacing: 4
            Text {
                text: qsTr("质量（CRF/CQ，越低越清晰）")
                color: Theme.textMuted
                font.pixelSize: Theme.fontSizeCaption
            }
            AppSpinBox {
                id: qualityValue
                Layout.fillWidth: true
                from: 0
                to: 63
                value: root.format ? Number(root.format.qualityValue) : 18
                editable: true
            }
        }
        ColumnLayout {
            Layout.fillWidth: true
            spacing: 4
            Text {
                text: qsTr("关键帧间隔（GOP）")
                color: Theme.textMuted
                font.pixelSize: Theme.fontSizeCaption
            }
            AppSpinBox {
                id: gopFrames
                Layout.fillWidth: true
                from: 1
                to: 600
                value: Math.max(1, Math.round(
                    workspaceController.profileFpsNumerator
                    / workspaceController.profileFpsDenominator * 2))
                editable: true
            }
        }
    }
    RowLayout {
        Layout.fillWidth: true
        visible: root.videoEnabled
        AppTextField { id: profileField; Layout.fillWidth: true; placeholderText: qsTr("Profile（可选）") }
        AppTextField { id: levelField; Layout.fillWidth: true; placeholderText: qsTr("Level（可选）") }
    }
    AppComboBox {
        id: scalingMethod
        Layout.fillWidth: true
        visible: root.videoEnabled
        textRole: "label"
        valueRole: "value"
        model: [
            {label: qsTr("缩放：Lanczos"), value: "lanczos"},
            {label: qsTr("缩放：双三次"), value: "bicubic"},
            {label: qsTr("缩放：双线性"), value: "bilinear"}
        ]
    }
    RowLayout {
        Layout.fillWidth: true
        visible: root.videoEnabled
        AppTextField { id: widthField; Layout.fillWidth: true; text: String(workspaceController.profileWidth); placeholderText: qsTr("宽") }
        Text { text: "×"; color: Theme.textMuted }
        AppTextField { id: heightField; Layout.fillWidth: true; text: String(workspaceController.profileHeight); placeholderText: qsTr("高") }
    }
    Text {
        text: qsTr("导出范围")
        color: Theme.text
        font.pixelSize: Theme.fontSizeBody
        font.weight: Font.DemiBold
    }
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
        visible: root.videoEnabled
        AppTextField { id: fpsNumerator; Layout.fillWidth: true; text: String(workspaceController.profileFpsNumerator); placeholderText: "FPS num" }
        Text { text: "/"; color: Theme.textMuted }
        AppTextField { id: fpsDenominator; Layout.fillWidth: true; text: String(workspaceController.profileFpsDenominator); placeholderText: "FPS den" }
    }
    AppTextField { id: targetBitrate; Layout.fillWidth: true; visible: root.videoEnabled; text: "0"; placeholderText: qsTr("目标码率 kbps（0=质量模式）") }
    AppTextField { id: maxBitrate; Layout.fillWidth: true; visible: root.videoEnabled; text: "0"; placeholderText: qsTr("最大码率 kbps（0=不限）") }
    AppTextField { id: audioCodecField; Layout.fillWidth: true; visible: root.videoEnabled; text: "aac"; placeholderText: qsTr("音频编码器") }
    RowLayout {
        Layout.fillWidth: true
        Text { text: qsTr("音频 kbps"); color: Theme.textMuted; font.pixelSize: Theme.fontSizeCaption }
        AppSpinBox { id: audioBitrate; Layout.fillWidth: true; from: 64; to: 1536; value: 192; editable: true }
        AppComboBox {
            id: audioChannels
            Layout.fillWidth: true
            textRole: "text"
            valueRole: "value"
            model: [
                {text: "Mono", value: 1},
                {text: "Stereo", value: 2},
                {text: "5.1", value: 6}
            ]
            currentIndex: 1
        }
    }
    AppTextField {
        id: maxCll
        Layout.fillWidth: true
        visible: workspaceController.colorMode === "hdr10_bt2020_pq" && root.videoEnabled
        text: "1000,400"
        placeholderText: "MaxCLL,MaxFALL"
    }
    AppTextField {
        id: masterDisplay
        Layout.fillWidth: true
        visible: workspaceController.colorMode === "hdr10_bt2020_pq" && root.videoEnabled
        text: "G(13250,34500)B(7500,3000)R(34000,16000)WP(15635,16450)L(10000000,1)"
        placeholderText: qsTr("母版显示元数据")
    }
    Text {
        Layout.fillWidth: true
        text: root.videoEnabled
            ? qsTr("导出后会实际运行 ffprobe，校验分辨率、编码、位深、色彩和 HDR 母版元数据。")
            : qsTr("导出后会实际运行 ffprobe，校验封装、音频编码和声道。")
        color: Theme.textMuted
        font.pixelSize: Theme.fontSizeCaption
        wrapMode: Text.WordWrap
    }
}

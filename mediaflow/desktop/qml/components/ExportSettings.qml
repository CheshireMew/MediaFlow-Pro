import QtQuick
import QtQuick.Layouts
import ".."

ColumnLayout {
    id: root

    property var format
    property var restoredEncoderPolicy: null
    property bool advancedOpen: false
    property string lastPreviewSignature: ""
    signal optionsChanged(var options)

    spacing: 10

    function encoderModel() {
        if (!root.format)
            return []
        const target = root.format.value
        const options = exportController.encoderPolicyOptions.filter(function(item) {
            return item.formats.indexOf(target) >= 0
        })
        if (target === "audio" || !root.restoredEncoderPolicy)
            return options
        const restoredValue = root.encoderPolicyValue(root.restoredEncoderPolicy)
        const exists = options.some(function(item) {
            return item.value === restoredValue
        })
        if (!exists) {
            options.push({
                label: qsTr("当前项目要求：%1（本机不可用）").arg(restoredValue),
                value: restoredValue,
                mode: root.restoredEncoderPolicy.mode,
                vendor: root.restoredEncoderPolicy.vendor || "auto",
                formats: [target],
                available: false
            })
        }
        return options
    }

    function encoderPolicyValue(policy) {
        return policy.mode === "software"
            ? "software" : String(policy.mode) + ":" + String(policy.vendor || "auto")
    }

    function restore(value) {
        const policy = value.encoder_policy || root.format.encoderPolicy || {mode: "software"}
        root.restoredEncoderPolicy = policy
        const policyValue = root.encoderPolicyValue(policy)
        const encoderIndex = encoderField.indexOfValue(policyValue)
        if (encoderField.count > 0)
            encoderField.currentIndex = encoderIndex >= 0 ? encoderIndex : 0
        technical.restore(value)
        subtitles.restore(value)
        watermark.restore(value)
    }

    function selectedEncoderPolicy() {
        if (encoderField.currentIndex < 0 || !encoderField.currentValue)
            return {mode: "software", vendor: "auto"}
        const components = String(encoderField.currentValue).split(":")
        return {
            mode: components[0],
            vendor: components.length > 1 && components[1].length > 0
                ? components[1] : "auto"
        }
    }

    function exportOptions() {
        const technicalOptions = technical.exportOptions()
        const subtitleOptions = subtitles.exportOptions()
        return {
            container: root.format.container || root.format.suffix,
            encoderPolicy: root.format.value === "audio"
                ? null : root.selectedEncoderPolicy(),
            pixelFormat: technicalOptions.pixelFormat,
            qualityValue: technicalOptions.qualityValue,
            preset: technicalOptions.preset,
            gopFrames: technicalOptions.gopFrames,
            audioCodec: technicalOptions.audioCodec,
            audioBitrate: technicalOptions.audioBitrate,
            burnSubtitleTrackId: root.format.value === "audio"
                ? "" : subtitleOptions.burnSubtitleTrackId,
            subtitleStyle: subtitleOptions.subtitleStyle,
            watermark: root.format.value === "audio" ? {enabled: false} : watermark.exportValue(),
            advanced: technicalOptions.advanced
        }
    }

    function publishPreviewOptions() {
        const options = root.exportOptions();
        const signature = JSON.stringify(options);
        if (signature === root.lastPreviewSignature)
            return;
        root.lastPreviewSignature = signature;
        root.optionsChanged(options);
    }

    Timer {
        interval: 180
        repeat: true
        running: root.visible
        triggeredOnStart: true
        onTriggered: root.publishPreviewOptions()
    }

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
                visible: root.format && root.format.value !== "audio"
                text: qsTr("编码器")
                color: Theme.textMuted
                font.pixelSize: Theme.fontSizeCaption
            }
            AppComboBox {
                id: encoderField
                objectName: "exportEncoderField"
                Layout.fillWidth: true
                visible: root.format && root.format.value !== "audio"
                model: root.encoderModel()
                textRole: "label"
                valueRole: "value"
                onCountChanged: {
                    if (count > 0 && currentIndex < 0)
                        currentIndex = 0
                }
            }
            Text {
                Layout.fillWidth: true
                visible: encoderField.currentIndex >= 0
                    && encoderField.model[encoderField.currentIndex]
                    && encoderField.model[encoderField.currentIndex].available === false
                text: qsTr("这个项目要求的硬件编码器在本机不可用。导出会停止并说明原因，不会悄悄改用其它编码器。")
                color: Theme.warning
                font.pixelSize: Theme.fontSizeCaption
                wrapMode: Text.WordWrap
            }
            Text {
                Layout.fillWidth: true
                visible: root.format && root.format.value === "audio"
                text: qsTr("编码器和封装会根据所选音频格式自动匹配。")
                color: Theme.textMuted
                font.pixelSize: Theme.fontSizeCaption
                wrapMode: Text.WordWrap
            }
        }
    }

    AppButton {
        objectName: "exportAdvancedToggle"
        Layout.fillWidth: true
        checkable: true
        checked: root.advancedOpen
        text: root.advancedOpen ? qsTr("收起高级设置") : qsTr("显示高级设置")
        onClicked: root.advancedOpen = !root.advancedOpen
    }

    Panel {
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

            ExportTechnicalSettings {
                id: technical
                Layout.fillWidth: true
                format: root.format
            }
            ExportSubtitleSettings {
                id: subtitles
                Layout.fillWidth: true
                videoEnabled: root.format && root.format.value !== "audio"
            }
            ExportWatermarkSettings {
                id: watermark
                Layout.fillWidth: true
                videoEnabled: root.format && root.format.value !== "audio"
            }
        }
    }
}

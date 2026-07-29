import QtQuick
import QtQuick.Layouts
import ".."

ColumnLayout {
    id: root

    property var format
    property bool advancedOpen: false
    property string lastPreviewSignature: ""
    signal optionsChanged(var options)

    spacing: 10

    function encoderModel() {
        if (!root.format)
            return []
        const target = root.format.value
        return exportController.videoEncoderOptions.filter(function(item) {
            return item.formats.indexOf(target) >= 0
        })
    }

    function restore(value) {
        const encoderIndex = encoderField.indexOfValue(value.video_codec || "")
        if (encoderField.count > 0)
            encoderField.currentIndex = encoderIndex >= 0 ? encoderIndex : 0
        technical.restore(value)
        subtitles.restore(value)
        watermark.restore(value)
    }

    function selectedEncoderCodec() {
        if (encoderField.currentIndex < 0
                || encoderField.currentValue === undefined
                || encoderField.currentValue === null)
            return ""
        return String(encoderField.currentValue)
    }

    function exportOptions() {
        const technicalOptions = technical.exportOptions()
        const subtitleOptions = subtitles.exportOptions()
        return {
            container: root.format.container || root.format.suffix,
            videoCodec: root.format.value === "audio" ? "" : root.selectedEncoderCodec(),
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

import QtQuick
import QtQuick.Dialogs
import QtQuick.Layouts
import ".."

ColumnLayout {
    id: root

    property bool videoEnabled: true

    Layout.fillWidth: true
    visible: videoEnabled
    spacing: 8

    function selectedAssetId() {
        return watermarkAsset.currentIndex >= 0 && watermarkAsset.currentValue
            ? String(watermarkAsset.currentValue) : ""
    }

    function exportValue() {
        return {
            enabled: watermarkEnabled.checked,
            asset_id: root.selectedAssetId() || null,
            position: String(watermarkPosition.currentValue),
            position_x: customWatermarkPosition.checked
                ? Number(watermarkPositionX.value) / 100 : null,
            position_y: customWatermarkPosition.checked
                ? Number(watermarkPositionY.value) / 100 : null,
            width_ratio: Number(watermarkWidth.value) / 100,
            opacity: Number(watermarkOpacity.value) / 100
        }
    }

    function restore(value) {
        const watermark = value.watermark || {}
        watermarkEnabled.checked = Boolean(watermark.enabled ?? false)
        const assetIndex = watermarkAsset.indexOfValue(
            watermark.asset_id || mediaController.selectedWatermarkAssetId || "")
        if (assetIndex >= 0)
            watermarkAsset.currentIndex = assetIndex
        const positionIndex = watermarkPosition.indexOfValue(watermark.position || "TR")
        if (positionIndex >= 0)
            watermarkPosition.currentIndex = positionIndex
        customWatermarkPosition.checked = watermark.position_x !== null
            && watermark.position_x !== undefined
            && watermark.position_y !== null
            && watermark.position_y !== undefined
        watermarkPositionX.value = Math.round(Number(watermark.position_x ?? 0.5) * 100)
        watermarkPositionY.value = Math.round(Number(watermark.position_y ?? 0.5) * 100)
        watermarkWidth.value = Math.round(Number(watermark.width_ratio ?? 0.2) * 100)
        watermarkOpacity.value = Math.round(Number(watermark.opacity ?? 1) * 100)
    }

    FileDialog {
        id: watermarkDialog
        title: qsTr("选择水印图片")
        fileMode: FileDialog.OpenFile
        nameFilters: [qsTr("图片 (*.png *.jpg *.jpeg *.webp *.bmp *.tif *.tiff)")]
        onAccepted: mediaController.importWatermark(selectedFile.toString())
    }

    Text {
        text: qsTr("水印")
        color: Theme.text
        font.pixelSize: Theme.fontSizeBody
        font.weight: Font.DemiBold
    }
    AppCheckBox { id: watermarkEnabled; text: qsTr("启用图片水印") }
    RowLayout {
        Layout.fillWidth: true
        AppComboBox {
            id: watermarkAsset
            Layout.fillWidth: true
            model: mediaController.watermarkAssetOptions
            textRole: "label"
            valueRole: "value"
            onActivated: mediaController.selectWatermarkAsset(String(currentValue))
        }
        AppButton { text: qsTr("导入图片"); onClicked: watermarkDialog.open() }
    }
    RowLayout {
        Layout.fillWidth: true
        visible: watermarkEnabled.checked
        AppComboBox {
            id: watermarkPosition
            Layout.fillWidth: true
            textRole: "label"
            valueRole: "value"
            model: [
                {label: qsTr("左上"), value: "TL"},
                {label: qsTr("上中"), value: "TC"},
                {label: qsTr("右上"), value: "TR"},
                {label: qsTr("左中"), value: "LC"},
                {label: qsTr("居中"), value: "C"},
                {label: qsTr("右中"), value: "RC"},
                {label: qsTr("左下"), value: "BL"},
                {label: qsTr("下中"), value: "BC"},
                {label: qsTr("右下"), value: "BR"}
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
        visible: watermarkEnabled.checked
        text: qsTr("自定义水印中心位置")
    }
    RowLayout {
        Layout.fillWidth: true
        visible: watermarkEnabled.checked && customWatermarkPosition.checked
        Text { text: "X%"; color: Theme.textMuted; font.pixelSize: Theme.fontSizeCaption }
        AppSpinBox { id: watermarkPositionX; Layout.fillWidth: true; from: 0; to: 100; value: 50; editable: true }
        Text { text: "Y%"; color: Theme.textMuted; font.pixelSize: Theme.fontSizeCaption }
        AppSpinBox { id: watermarkPositionY; Layout.fillWidth: true; from: 0; to: 100; value: 50; editable: true }
    }
}

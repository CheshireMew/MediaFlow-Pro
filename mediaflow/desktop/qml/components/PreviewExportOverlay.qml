import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import ".."

Item {
    id: overlay
    property bool active: false
    property var watermark: ({})
    property var subtitleStyle: ({})
    property url watermarkSource: ""
    property string subtitleText: ""
    property int profileWidth: 1920
    property int profileHeight: 1080

    Image {
        id: exportWatermarkPreview
        objectName: "exportWatermarkPreview"
        readonly property real widthRatio: Number(overlay.watermark.width_ratio || 0.2)
        readonly property real heightRatio: Math.min(1.0, overlay.width * widthRatio * Math.max(1, implicitHeight) / (Math.max(1, implicitWidth) * overlay.height))
        readonly property real marginX: overlay.profileHeight > overlay.profileWidth ? 0.045 : 0.03
        readonly property real marginY: overlay.profileHeight > overlay.profileWidth ? 0.035 : 0.05
        readonly property string placement: String(overlay.watermark.position || "TR")
        readonly property real centerXRatio: overlay.watermark.position_x !== null && overlay.watermark.position_x !== undefined ? Number(overlay.watermark.position_x) : placement.indexOf("L") >= 0 ? marginX + widthRatio / 2 : placement.indexOf("R") >= 0 ? 1 - marginX - widthRatio / 2 : 0.5
        readonly property real centerYRatio: overlay.watermark.position_y !== null && overlay.watermark.position_y !== undefined ? Number(overlay.watermark.position_y) : placement.indexOf("T") >= 0 ? marginY + heightRatio / 2 : placement.indexOf("B") >= 0 ? 1 - marginY - heightRatio / 2 : 0.5
        visible: overlay.active && source.toString().length > 0 && Boolean(overlay.watermark.enabled)
        source: overlay.watermarkSource
        width: overlay.width * widthRatio
        height: overlay.height * heightRatio
        x: Math.max(0, Math.min(overlay.width - width, overlay.width * centerXRatio - width / 2))
        y: Math.max(0, Math.min(overlay.height - height, overlay.height * centerYRatio - height / 2))
        opacity: Number(overlay.watermark.opacity ?? 1)
        fillMode: Image.Stretch
        smooth: true
    }

    Item {
        id: subtitlePreviewGeometry
        objectName: "exportSubtitlePreview"
        readonly property bool styled: overlay.active
        readonly property real positionX: Number(overlay.subtitleStyle.position_x ?? 0.5)
        readonly property real positionY: Number(overlay.subtitleStyle.position_y ?? 0.88)
        width: parent.width * 0.9
        height: parent.height * 0.25
        x: Math.max(0, Math.min(parent.width - width, parent.width * positionX - width / 2))
        y: Math.max(0, Math.min(parent.height - height, parent.height * positionY - height / 2))
        visible: overlay.subtitleText.length > 0

        Rectangle {
            anchors.fill: subtitlePreviewText
            anchors.margins: -Math.max(0, Number(overlay.subtitleStyle.background_padding || 0)) * overlay.height / 540
            visible: subtitlePreviewGeometry.styled && Boolean(overlay.subtitleStyle.background_enabled)
            color: overlay.subtitleStyle.background_color || "#000000"
            opacity: Number(overlay.subtitleStyle.background_opacity || 0)
            radius: 2
        }
        Text {
            id: subtitlePreviewText
            objectName: "exportSubtitlePreviewText"
            anchors.fill: parent
            text: overlay.subtitleText
            color: subtitlePreviewGeometry.styled ? overlay.subtitleStyle.font_color || "#FFFFFF" : "white"
            font.family: subtitlePreviewGeometry.styled ? overlay.subtitleStyle.font_family || "Microsoft YaHei UI" : "Microsoft YaHei UI"
            font.pixelSize: subtitlePreviewGeometry.styled ? Math.max(8, Number(overlay.subtitleStyle.font_size || 24) * overlay.height / 540) : Math.max(18, overlay.height * 0.055)
            font.weight: subtitlePreviewGeometry.styled && Boolean(overlay.subtitleStyle.bold) ? Font.Bold : Font.DemiBold
            font.italic: subtitlePreviewGeometry.styled && Boolean(overlay.subtitleStyle.italic)
            style: Text.Outline
            styleColor: subtitlePreviewGeometry.styled ? overlay.subtitleStyle.outline_color || "#000000" : "black"
            wrapMode: Text.WordWrap
            horizontalAlignment: !subtitlePreviewGeometry.styled || overlay.subtitleStyle.alignment === "center" ? Text.AlignHCenter : overlay.subtitleStyle.alignment === "right" ? Text.AlignRight : Text.AlignLeft
            verticalAlignment: !subtitlePreviewGeometry.styled || overlay.subtitleStyle.multiline_alignment === "center" ? Text.AlignVCenter : overlay.subtitleStyle.multiline_alignment === "bottom" ? Text.AlignBottom : Text.AlignTop
        }
    }
}

import QtQuick
import QtQuick.Layouts
import ".."

Panel {
    implicitHeight: 58
    RowLayout {
        anchors.fill: parent
        anchors.margins: 11
        spacing: 8
        ColumnLayout {
            Layout.fillWidth: true
            spacing: 2
            Text {
                text: qsTr("当前序列")
                color: Theme.textMuted
                font.pixelSize: Theme.fontSizeCaption
            }
            Text {
                Layout.fillWidth: true
                text: workspaceController.profileLabel
                color: Theme.text
                font.pixelSize: Theme.fontSizeBody
                font.weight: Font.DemiBold
                elide: Text.ElideRight
            }
        }
        Text {
            visible: workspaceController.profileConfirmed
            text: workspaceController.colorMode === "hdr10_bt2020_pq"
                ? "HDR10" : "SDR"
            color: workspaceController.colorMode === "hdr10_bt2020_pq"
                ? Theme.warning : Theme.accentHover
            font.pixelSize: Theme.fontSizeCaption
        }
    }
}

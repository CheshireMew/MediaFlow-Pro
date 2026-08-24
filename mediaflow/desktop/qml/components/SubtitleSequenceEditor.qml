import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Dialogs
import ".."

ColumnLayout {
    id: editor
    spacing: 7
    readonly property bool canEdit: Boolean(mediaflow.workspaceViewController.actionCapabilities.canEdit)
    RowLayout {
        Layout.fillWidth: true
        Text {
            text: qsTr("序列字幕")
            color: Theme.text
            font.pixelSize: Theme.fontSizeBodySmall
            font.weight: Font.DemiBold
        }
        Item {
            Layout.fillWidth: true
        }
        AppButton {
            text: qsTr("放入当前序列")
            primary: true
            enabled: editor.canEdit && mediaflow.subtitleViewController.selectedDocumentId.length > 0
            onClicked: mediaflow.subtitlePlacementController.placeSubtitleDocument(mediaflow.subtitleViewController.selectedDocumentId)
        }
    }
    ListView {
        id: placementList
        Layout.fillWidth: true
        Layout.preferredHeight: Math.max(160, Math.min(360, contentHeight))
        clip: true
        spacing: 4
        model: mediaflow.subtitleViewController.subtitlePlacementsModel
        delegate: Rectangle {
            required property string placementId
            required property int startFrame
            required property int endFrame
            required property string text
            required property bool hasOverride
            width: placementList.width
            height: 48
            radius: Theme.radiusSmall
            color: mediaflow.subtitleViewController.selectedSubtitlePlacementId === placementId ? Theme.accentSoft : placementMouse.containsMouse ? Theme.surfaceHover : Theme.surfaceRaised
            border.color: hasOverride ? Theme.accent : Theme.border
            RowLayout {
                anchors.fill: parent
                anchors.margins: 7
                Text {
                    text: startFrame + "–" + endFrame
                    color: Theme.textMuted
                    font.pixelSize: Theme.fontSizeCaption
                }
                Text {
                    Layout.fillWidth: true
                    text: parent.parent.text
                    color: Theme.text
                    elide: Text.ElideRight
                    font.pixelSize: Theme.fontSizeCaption
                }
                Text {
                    visible: hasOverride
                    text: qsTr("序列覆盖")
                    color: Theme.accentHover
                    font.pixelSize: Theme.fontSizeCaption
                }
            }
            MouseArea {
                id: placementMouse
                anchors.fill: parent
                hoverEnabled: true
                onClicked: mediaflow.subtitleViewController.selectSubtitlePlacement(placementId)
            }
        }
        EmptyState {
            anchors.fill: parent
            visible: placementList.count === 0
            iconName: "subtitle"
            title: qsTr("序列中还没有字幕")
            description: qsTr("选择字幕文档并放入当前序列。")
        }
    }
    Panel {
        Layout.fillWidth: true
        implicitHeight: 152
        visible: mediaflow.subtitleViewController.selectedSubtitlePlacementId.length > 0
        ColumnLayout {
            anchors.fill: parent
            anchors.margins: 8
            spacing: 6
            AppTextArea {
                id: placementText
                collaborationPath: "/subtitles/placements/" + mediaflow.subtitleViewController.selectedSubtitlePlacementId + "/text"
                Layout.fillWidth: true
                Layout.fillHeight: true
                text: mediaflow.subtitleViewController.selectedSubtitlePlacementData.text || ""
                wrapMode: TextEdit.Wrap
                readOnly: !editor.canEdit
            }
            RowLayout {
                Layout.fillWidth: true
                AppButton {
                    Layout.fillWidth: true
                    primary: true
                    text: qsTr("保存为序列覆盖")
                    enabled: editor.canEdit
                    onClicked: mediaflow.subtitlePlacementController.updateSubtitlePlacementText(mediaflow.subtitleViewController.selectedSubtitlePlacementId, placementText.text, false)
                }
                AppButton {
                    Layout.fillWidth: true
                    text: qsTr("应用到文档")
                    enabled: editor.canEdit
                    onClicked: mediaflow.subtitlePlacementController.updateSubtitlePlacementText(mediaflow.subtitleViewController.selectedSubtitlePlacementId, placementText.text, true)
                }
            }
        }
    }
}

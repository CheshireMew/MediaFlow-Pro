import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "."
import "components"

Item {
    id: trackControlsPanel
    required property var view
    required property real scrollY
    objectName: "trackControlsPanel"
    anchors.top: parent.top
    anchors.topMargin: 44
    anchors.left: parent.left
    anchors.bottom: parent.bottom
    width: Math.min(view.trackControlsWidth, view.width)
    visible: trackControlsRepeater.count > 0
    clip: true
    z: 10

    Rectangle {
        anchors.fill: parent
        color: Theme.timelineBackground
    }

    Rectangle {
        anchors.top: parent.top
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        width: 1
        color: Theme.divider
    }

    Rectangle {
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        height: 28
        color: Theme.timelineRuler
        border.color: Theme.timelineGrid

        RowLayout {
            anchors.fill: parent
            anchors.leftMargin: 8
            anchors.rightMargin: 5
            spacing: 5
            Text {
                Layout.fillWidth: true
                text: qsTr("轨道")
                color: Theme.textMuted
                font.pixelSize: Theme.fontSizeCaption
                font.weight: Font.DemiBold
            }
            AppIconButton {
                id: addTrackButton
                objectName: "addTrackButton"
                iconName: "add"
                iconSize: 15
                flat: false
                implicitWidth: 24
                implicitHeight: 22
                enabled: view.canEdit
                Accessible.name: qsTr("添加轨道")
                toolTipText: qsTr("添加轨道")
                onClicked: addTrackMenu.open()
                AppMenu {
                    id: addTrackMenu
                    y: addTrackButton.height + 3
                    AppMenuItem {
                        text: qsTr("视频轨")
                        enabled: view.canEdit
                        onTriggered: mediaflow.timelineStructureController.addTrack("video")
                    }
                    AppMenuItem {
                        text: qsTr("音频轨")
                        enabled: view.canEdit
                        onTriggered: mediaflow.timelineStructureController.addTrack("audio")
                    }
                    AppMenuItem {
                        text: qsTr("字幕轨")
                        enabled: view.canEdit
                        onTriggered: mediaflow.timelineStructureController.addTrack("subtitle")
                    }
                }
            }
        }
    }

    Column {
        y: 28 - scrollY
        width: parent.width
        spacing: 1

        Repeater {
            id: trackControlsRepeater
            model: mediaflow.timelineViewController.tracksModel
            delegate: Rectangle {
                required property string trackId
                required property string displayName
                required property string kind
                required property int position
                required property bool locked
                required property bool muted
                required property bool solo
                required property bool primaryDialogue
                required property string audioBusId
                required property var model

                objectName: "trackControlsOverlay"
                width: trackControlsPanel.width
                height: view.trackHeight
                color: primaryDialogue
                    ? Theme.transcriptTrackSoft
                    : position % 2 === 0 ? Theme.timelineTrackA : Theme.timelineTrackB
                border.color: primaryDialogue ? Theme.transcriptTrack : Theme.timelineGrid
                border.width: 1

                Rectangle {
                    objectName: "primaryDialogueTrackMarker"
                    visible: primaryDialogue
                    anchors.left: parent.left
                    anchors.top: parent.top
                    anchors.bottom: parent.bottom
                    width: 3
                    color: Theme.transcriptTrack
                }

                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 6
                    spacing: 3
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 6
                        Item {
                            Layout.preferredWidth: kind === "audio" ? (primaryDialogue ? 54 : 24) : 22
                            Layout.preferredHeight: 22

                            AppIcon {
                                visible: kind !== "audio"
                                anchors.centerIn: parent
                                width: 17
                                height: 17
                                iconName: kind === "video" ? "media" : "subtitle"
                                iconColor: Theme.textMuted
                            }

                            AbstractButton {
                                id: primaryDialogueButton
                                visible: kind === "audio"
                                objectName: "primaryDialogueButton"
                                anchors.fill: parent
                                checkable: true
                                checked: primaryDialogue
                                enabled: view.canEdit
                                text: primaryDialogue ? qsTr("转录") : ""
                                leftPadding: 3
                                rightPadding: 3
                                Accessible.name: primaryDialogue ? qsTr("当前转录轨道") : qsTr("设为转录轨道")
                                ToolTip.visible: hovered
                                ToolTip.text: primaryDialogue ? qsTr("转录只读取这条轨道") : qsTr("设为转录轨道；转录将只读取这条轨道")
                                onClicked: mediaflow.timelineStructureController.setPrimaryDialogueTrack(trackId)

                                contentItem: Row {
                                    spacing: 4
                                    anchors.centerIn: parent
                                    AppIcon {
                                        width: 14
                                        height: 14
                                        objectName: "primaryDialogueMicrophone"
                                        iconName: "microphone"
                                        iconColor: primaryDialogue
                                            ? Theme.transcriptTrack : Theme.audio
                                    }
                                    Text {
                                        visible: primaryDialogue
                                        text: qsTr("转录")
                                        color: Theme.transcriptTrackHover
                                        font.pixelSize: Theme.fontSizeCaption
                                        font.weight: Font.Bold
                                        anchors.verticalCenter: parent.verticalCenter
                                    }
                                }
                                background: Rectangle {
                                    objectName: "primaryDialogueButtonBackground"
                                    radius: Theme.radiusSmall
                                    color: primaryDialogue ? Theme.transcriptTrackSoft : primaryDialogueButton.hovered ? Theme.surfaceHover : Theme.surfaceSunken
                                    border.color: primaryDialogue ? Theme.transcriptTrack : primaryDialogueButton.hovered ? Theme.audio : Theme.border
                                    border.width: primaryDialogue ? 2 : 1
                                }
                            }
                        }
                        Text {
                            Layout.fillWidth: true
                            text: displayName
                            color: !model.enabled
                                ? Theme.textDisabled
                                : primaryDialogue ? Theme.transcriptTrackHover : Theme.text
                            font.pixelSize: Theme.fontSizeCaption
                            font.weight: primaryDialogue ? Font.DemiBold : Font.Normal
                            elide: Text.ElideRight
                        }
                        Text {
                            text: position + 1
                            color: Theme.textMuted
                            font.pixelSize: Theme.fontSizeCaption
                        }
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 3
                        AppIconButton {
                            iconName: model.enabled ? "check" : "close"
                            iconSize: 14
                            flat: false
                            enabled: view.canEdit
                            implicitWidth: 24
                            implicitHeight: 22
                            Accessible.name: model.enabled ? qsTr("禁用轨道") : qsTr("启用轨道")
                            toolTipText: Accessible.name
                            onClicked: mediaflow.timelineStructureController.updateTrack(trackId, !model.enabled, locked, muted, solo, audioBusId)
                        }
                        AppIconButton {
                            iconName: locked ? "lock" : "unlock"
                            iconSize: 14
                            flat: false
                            enabled: view.canEdit
                            implicitWidth: 24
                            implicitHeight: 22
                            Accessible.name: locked ? qsTr("解锁轨道") : qsTr("锁定轨道")
                            toolTipText: Accessible.name
                            onClicked: mediaflow.timelineStructureController.updateTrack(trackId, model.enabled, !locked, muted, solo, audioBusId)
                        }
                        Loader {
                            active: kind === "video" || kind === "audio"
                            visible: active
                            Layout.preferredWidth: 24
                            Layout.preferredHeight: 22
                            sourceComponent: AppButton {
                                objectName: "trackMuteButton_" + trackId
                                text: "M"
                                enabled: view.canEdit
                                checkable: true
                                checked: muted
                                implicitWidth: 24
                                implicitHeight: 22
                                leftPadding: 0
                                rightPadding: 0
                                font.pixelSize: Theme.fontSizeCaption
                                Accessible.name: muted ? qsTr("取消静音") : qsTr("静音")
                                ToolTip.visible: hovered
                                ToolTip.text: Accessible.name
                                onClicked: mediaflow.timelineStructureController.updateTrack(trackId, model.enabled, locked, !muted, solo, audioBusId)
                            }
                        }
                        AppButton {
                            text: "S"
                            enabled: view.canEdit
                            checkable: true
                            checked: solo
                            implicitWidth: 24
                            implicitHeight: 22
                            leftPadding: 0
                            rightPadding: 0
                            font.pixelSize: Theme.fontSizeCaption
                            Accessible.name: solo ? qsTr("取消独奏") : qsTr("独奏")
                            ToolTip.visible: hovered
                            ToolTip.text: Accessible.name
                            onClicked: mediaflow.timelineStructureController.updateTrack(trackId, model.enabled, locked, muted, !solo, audioBusId)
                        }
                        Item {
                            Layout.fillWidth: true
                        }
                        AppIconButton {
                            iconName: "up"
                            iconSize: 14
                            flat: false
                            enabled: view.canEdit && position > 0
                            implicitWidth: 24
                            implicitHeight: 22
                            Accessible.name: qsTr("轨道上移")
                            toolTipText: Accessible.name
                            onClicked: mediaflow.timelineStructureController.moveTrack(trackId, position - 1)
                        }
                        AppIconButton {
                            iconName: "down"
                            iconSize: 14
                            flat: false
                            enabled: view.canEdit && position + 1 < mediaflow.timelineViewController.tracksModel.rowCount()
                            implicitWidth: 24
                            implicitHeight: 22
                            Accessible.name: qsTr("轨道下移")
                            toolTipText: Accessible.name
                            onClicked: mediaflow.timelineStructureController.moveTrack(trackId, position + 1)
                        }
                    }
                }
            }
        }
    }
}

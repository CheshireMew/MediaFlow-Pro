import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import ".."

Rectangle {
    id: root
    objectName: "workspaceNavigation"

    property string activeMode: "media"
    property bool compactInspectorAvailable: false
    property bool inspectorDrawerOpen: false
    signal modeRequested(string mode)
    signal settingsRequested
    signal inspectorDrawerToggled

    implicitHeight: 50
    color: Theme.surfaceRaised
    border.color: Theme.border

    RowLayout {
        anchors.fill: parent
        anchors.leftMargin: 12
        anchors.rightMargin: 12
        anchors.topMargin: 6
        anchors.bottomMargin: 6
        spacing: 5

        Repeater {
            model: [
                {
                    key: "media",
                    label: qsTr("媒体")
                },
                {
                    key: "transcript",
                    label: qsTr("转录")
                },
                {
                    key: "translate",
                    label: qsTr("翻译")
                },
                {
                    key: "highlight",
                    label: qsTr("高光")
                },
                {
                    key: "edit",
                    label: qsTr("编辑")
                },
                {
                    key: "audio",
                    label: qsTr("音频")
                },
                {
                    key: "export",
                    label: qsTr("导出")
                }
            ]
            Rectangle {
                id: navigationItem
                required property var modelData
                objectName: "navigationItem_" + modelData.key
                Layout.preferredWidth: 84
                Layout.fillHeight: true
                radius: Theme.radiusSmall
                color: root.activeMode === modelData.key ? Theme.accentSoft : navMouse.containsMouse ? Theme.surfaceHover : "transparent"
                border.color: activeFocus ? Theme.accentHover : root.activeMode === modelData.key ? Theme.accent : "transparent"
                border.width: activeFocus ? 2 : 1
                RowLayout {
                    anchors.centerIn: parent
                    spacing: 6
                    NavIcon {
                        Layout.preferredWidth: 17
                        Layout.preferredHeight: 17
                        iconName: modelData.key
                        iconColor: root.activeMode === modelData.key ? Theme.accentHover : Theme.textMuted
                    }
                    Text {
                        text: modelData.label
                        color: root.activeMode === modelData.key ? Theme.text : Theme.textMuted
                        font.pixelSize: Theme.fontSizeBodySmall
                        font.weight: root.activeMode === modelData.key ? Font.DemiBold : Font.Medium
                    }
                }
                Accessible.name: modelData.label
                Accessible.role: Accessible.Button
                activeFocusOnTab: true
                Keys.onReturnPressed: root.modeRequested(modelData.key)
                Keys.onSpacePressed: root.modeRequested(modelData.key)
                ToolTip.visible: navMouse.containsMouse
                ToolTip.text: modelData.label
                MouseArea {
                    id: navMouse
                    anchors.fill: parent
                    hoverEnabled: true
                    onClicked: {
                        navigationItem.forceActiveFocus();
                        root.modeRequested(modelData.key);
                    }
                }
            }
        }
        Item {
            Layout.fillWidth: true
        }
        AppButton {
            objectName: "compactInspectorButton"
            Layout.fillHeight: true
            visible: root.compactInspectorAvailable
            checkable: true
            checked: root.inspectorDrawerOpen
            text: qsTr("检查器")
            onClicked: root.inspectorDrawerToggled()
        }
        Rectangle {
            Layout.preferredWidth: 1
            Layout.preferredHeight: 24
            color: Theme.borderStrong
        }
        Rectangle {
            id: settingsItem
            objectName: "navigationItem_settings"
            Layout.preferredWidth: 84
            Layout.fillHeight: true
            radius: Theme.radiusSmall
            color: settingsMouse.containsMouse ? Theme.surfaceHover : "transparent"
            border.color: activeFocus ? Theme.accentHover : "transparent"
            border.width: activeFocus ? 2 : 1
            RowLayout {
                anchors.centerIn: parent
                spacing: 6
                NavIcon {
                    Layout.preferredWidth: 17
                    Layout.preferredHeight: 17
                    iconName: "settings"
                    iconColor: Theme.textMuted
                }
                Text {
                    text: qsTr("设置")
                    color: Theme.textMuted
                    font.pixelSize: Theme.fontSizeBodySmall
                    font.weight: Font.Medium
                }
            }
            Accessible.name: qsTr("设置")
            Accessible.role: Accessible.Button
            activeFocusOnTab: true
            Keys.onReturnPressed: root.settingsRequested()
            Keys.onSpacePressed: root.settingsRequested()
            ToolTip.visible: settingsMouse.containsMouse
            ToolTip.text: qsTr("设置")
            MouseArea {
                id: settingsMouse
                anchors.fill: parent
                hoverEnabled: true
                onClicked: {
                    settingsItem.forceActiveFocus();
                    root.settingsRequested();
                }
            }
        }
    }
}

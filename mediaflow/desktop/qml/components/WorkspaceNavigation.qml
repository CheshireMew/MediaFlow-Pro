import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import ".."

Rectangle {
    id: root
    objectName: "workspaceNavigation"

    readonly property var modes: workspaceController.workspaceModes
    property string activeMode: modes.length > 0 ? String(modes[0].key) : ""
    signal modeRequested(string mode)
    signal settingsRequested

    implicitHeight: 54
    color: Theme.surface

    RowLayout {
        anchors.fill: parent
        anchors.leftMargin: 16
        anchors.rightMargin: 16
        anchors.topMargin: 8
        anchors.bottomMargin: 8
        spacing: 5

        Repeater {
            model: root.modes

            Rectangle {
                id: navigationItem
                required property var modelData
                readonly property bool selected: root.activeMode === String(modelData.key)
                objectName: "navigationItem_" + modelData.key

                function click() {
                    root.modeRequested(modelData.key);
                }

                Layout.minimumWidth: 84
                Layout.preferredWidth: Math.max(84, navigationContent.implicitWidth + 28)
                Layout.fillHeight: true
                radius: Theme.radiusSmall
                color: navigationItem.selected
                    ? Theme.accentSoft
                    : navMouse.containsMouse ? Theme.surfaceHover : Theme.transparent
                border.color: activeFocus
                    ? Theme.focusColor
                    : navigationItem.selected ? Theme.borderStrong : Theme.transparent
                border.width: activeFocus || navigationItem.selected ? 1 : 0

                RowLayout {
                    id: navigationContent
                    anchors.centerIn: parent
                    spacing: 6

                    AppIcon {
                        Layout.preferredWidth: 17
                        Layout.preferredHeight: 17
                        iconName: String(modelData.icon)
                        iconColor: navigationItem.selected ? Theme.accentHover : Theme.textMuted
                    }

                    Text {
                        text: modelData.label
                        color: navigationItem.selected ? Theme.text : Theme.textMuted
                        font.pixelSize: Theme.fontSizeBodySmall
                        font.weight: navigationItem.selected ? Font.DemiBold : Font.Medium
                    }
                }

                Accessible.name: modelData.label
                Accessible.role: Accessible.Button
                activeFocusOnTab: true
                Keys.onReturnPressed: navigationItem.click()
                Keys.onSpacePressed: navigationItem.click()

                MouseArea {
                    id: navMouse
                    anchors.fill: parent
                    hoverEnabled: true
                    onClicked: {
                        navigationItem.forceActiveFocus();
                        navigationItem.click();
                    }
                }
            }
        }
        Item {
            Layout.fillWidth: true
        }
        Rectangle {
            Layout.preferredWidth: 1
            Layout.preferredHeight: 20
            color: Theme.divider
        }

        Rectangle {
            id: settingsItem
            objectName: "navigationItem_settings"
            Layout.minimumWidth: 84
            Layout.preferredWidth: Math.max(84, settingsContent.implicitWidth + 28)
            Layout.fillHeight: true
            radius: Theme.radiusSmall
            color: settingsMouse.containsMouse ? Theme.surfaceHover : Theme.transparent
            border.color: activeFocus ? Theme.focusColor : Theme.transparent
            border.width: activeFocus ? 1 : 0

            RowLayout {
                id: settingsContent
                anchors.centerIn: parent
                spacing: 6

                AppIcon {
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

    Rectangle {
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        height: 1
        color: Theme.divider
    }
}

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import ".."

Rectangle {
    id: root
    objectName: "workspaceNavigation"

    readonly property var modes: workspaceController.workspaceModes.filter(
        function (mode) { return Boolean(mode.navigationVisible); })
    property string activeMode: modes.length > 0 ? String(modes[0].key) : ""
    signal modeRequested(string mode)
    signal settingsRequested

    implicitHeight: 68
    color: Theme.surface

    RowLayout {
        anchors.fill: parent
        anchors.leftMargin: 8
        anchors.rightMargin: 8
        anchors.topMargin: 5
        anchors.bottomMargin: 4
        spacing: 1

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

                Layout.minimumWidth: 58
                Layout.preferredWidth: Math.max(58, navigationContent.implicitWidth + 14)
                Layout.fillHeight: true
                radius: Theme.radiusSmall
                color: navMouse.containsMouse ? Theme.surfaceHover : Theme.transparent
                border.color: activeFocus
                    ? Theme.focusColor
                    : Theme.transparent
                border.width: activeFocus ? 1 : 0

                ColumnLayout {
                    id: navigationContent
                    anchors.centerIn: parent
                    spacing: 3

                    AppIcon {
                        Layout.alignment: Qt.AlignHCenter
                        Layout.preferredWidth: 22
                        Layout.preferredHeight: 22
                        iconName: String(modelData.icon)
                        iconColor: navigationItem.selected ? Theme.accent : Theme.textSubtle
                    }

                    Text {
                        Layout.alignment: Qt.AlignHCenter
                        text: modelData.label
                        color: navigationItem.selected ? Theme.accent : Theme.textSubtle
                        font.pixelSize: Theme.fontSizeBodySmall
                        font.weight: navigationItem.selected ? Font.DemiBold : Font.Medium
                    }
                }

                Rectangle {
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.bottom: parent.bottom
                    anchors.leftMargin: 12
                    anchors.rightMargin: 12
                    height: 2
                    radius: 1
                    visible: navigationItem.selected
                    color: Theme.accent
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
            id: settingsItem
            objectName: "navigationItem_settings"

            function click() {
                root.settingsRequested();
            }

            Layout.minimumWidth: 58
            Layout.preferredWidth: Math.max(58, settingsContent.implicitWidth + 14)
            Layout.fillHeight: true
            radius: Theme.radiusSmall
            color: settingsMouse.containsMouse ? Theme.surfaceHover : Theme.transparent
            border.color: activeFocus ? Theme.focusColor : Theme.transparent
            border.width: activeFocus ? 1 : 0

            ColumnLayout {
                id: settingsContent
                anchors.centerIn: parent
                spacing: 3

                AppIcon {
                    Layout.alignment: Qt.AlignHCenter
                    Layout.preferredWidth: 22
                    Layout.preferredHeight: 22
                    iconName: "settings"
                    iconColor: Theme.textSubtle
                }
                Text {
                    Layout.alignment: Qt.AlignHCenter
                    text: qsTr("设置")
                    color: Theme.textSubtle
                    font.pixelSize: Theme.fontSizeBodySmall
                    font.weight: Font.Medium
                }
            }
            Accessible.name: qsTr("设置")
            Accessible.role: Accessible.Button
            activeFocusOnTab: true
            Keys.onReturnPressed: settingsItem.click()
            Keys.onSpacePressed: settingsItem.click()

            MouseArea {
                id: settingsMouse
                anchors.fill: parent
                hoverEnabled: true
                onClicked: {
                    settingsItem.forceActiveFocus();
                    settingsItem.click();
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

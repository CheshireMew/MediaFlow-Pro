import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import ".."

Item {
    id: root
    objectName: "workspaceTour"
    visible: false
    z: 10000
    property int step: 0
    readonly property var steps: [
        { title: qsTr("素材与内容时刻"), body: qsTr("双击素材可在源监视器中预览并设入出点；搜索还能直接定位转写和画面高光。"), x: 0.02, y: 0.10, w: 0.29, h: 0.48 },
        { title: qsTr("源 / 节目监视器"), body: qsTr("源监视器负责挑选素材范围，节目监视器显示时间线的最终结果。"), x: 0.32, y: 0.10, w: 0.40, h: 0.48 },
        { title: qsTr("检查器"), body: qsTr("在这里换源、批量调整片段，并建立会进入预览和导出的视觉效果链。"), x: 0.73, y: 0.10, w: 0.25, h: 0.48 },
        { title: qsTr("时间线与序列"), body: qsTr("底部可切换序列、拖动淡入淡出和转场。标题栏还能切换工作区布局并查看全局任务。"), x: 0.02, y: 0.60, w: 0.96, h: 0.37 }
    ]
    readonly property var current: steps[Math.max(0, Math.min(step, steps.length - 1))]
    readonly property rect target: Qt.rect(
        Math.round(width * current.x), Math.round(height * current.y),
        Math.round(width * current.w), Math.round(height * current.h))

    function open() {
        step = 0;
        visible = true;
        forceActiveFocus();
    }
    function finish() {
        visible = false;
        settingsController.setWorkspaceTourCompleted(true);
    }

    Keys.onEscapePressed: root.finish()
    Rectangle { x: 0; y: 0; width: root.width; height: root.target.y; color: "#a6000000" }
    Rectangle { x: 0; y: root.target.y; width: root.target.x; height: root.target.height; color: "#a6000000" }
    Rectangle { x: root.target.x + root.target.width; y: root.target.y; width: root.width - x; height: root.target.height; color: "#a6000000" }
    Rectangle { x: 0; y: root.target.y + root.target.height; width: root.width; height: root.height - y; color: "#a6000000" }
    Rectangle {
        x: root.target.x; y: root.target.y
        width: root.target.width; height: root.target.height
        color: "transparent"
        border.width: 2
        border.color: Theme.accent
        radius: Theme.radius
    }
    MouseArea { anchors.fill: parent }

    Panel {
        id: card
        width: Math.min(390, root.width - 32)
        implicitHeight: cardContent.implicitHeight + 24
        x: Math.max(16, Math.min(root.width - width - 16,
            root.step < 3 ? root.target.x + 18 : root.target.x + root.target.width - width - 18))
        y: root.step < 3
            ? Math.min(root.height - height - 16, root.target.y + root.target.height + 14)
            : Math.max(16, root.target.y - height - 14)
        color: Theme.surfaceRaised
        border.color: Theme.accent
        ColumnLayout {
            id: cardContent
            anchors.fill: parent
            anchors.margins: 12
            spacing: 8
            Text {
                Layout.fillWidth: true
                text: root.current.title
                color: Theme.text
                font.pixelSize: Theme.fontSizeBodyLarge
                font.weight: Font.DemiBold
            }
            Text {
                Layout.fillWidth: true
                text: root.current.body
                color: Theme.textSubtle
                font.pixelSize: Theme.fontSizeBodySmall
                wrapMode: Text.WordWrap
            }
            RowLayout {
                Layout.fillWidth: true
                Text {
                    Layout.fillWidth: true
                    text: qsTr("%1 / %2").arg(root.step + 1).arg(root.steps.length)
                    color: Theme.textMuted
                    font.pixelSize: Theme.fontSizeCaption
                }
                AppButton { text: qsTr("跳过"); quiet: true; onClicked: root.finish() }
                AppButton {
                    primary: true
                    text: root.step + 1 === root.steps.length ? qsTr("完成") : qsTr("下一步")
                    onClicked: root.step + 1 === root.steps.length
                        ? root.finish() : root.step += 1
                }
            }
        }
    }
}

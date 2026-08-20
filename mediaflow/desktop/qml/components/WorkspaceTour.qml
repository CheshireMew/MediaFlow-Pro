import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import ".."

Item {
    id: root
    objectName: "workspaceTour"
    visible: false
    z: 10000
    property Item workspaceItem: null
    property int step: 0
    property rect target: Qt.rect(0, 0, 0, 0)
    readonly property real targetPadding: 5
    readonly property real cardGap: 12
    readonly property var steps: [
        { title: qsTr("素材与内容时刻"), body: qsTr("双击素材可在源监视器中预览并设入出点；搜索还能直接定位转写和画面高光。"), targetKey: "tool" },
        { title: qsTr("源 / 节目监视器"), body: qsTr("源监视器负责挑选素材范围，节目监视器显示时间线的最终结果。"), targetKey: "preview" },
        { title: qsTr("检查器"), body: qsTr("在这里换源、批量调整片段，并建立会进入预览和导出的视觉效果链。"), targetKey: "inspector" },
        { title: qsTr("时间线与序列"), body: qsTr("底部可切换序列、拖动淡入淡出和转场。标题栏还能切换工作区布局并查看全局任务。"), targetKey: "timeline" }
    ]
    readonly property var current: steps[Math.max(0, Math.min(step, steps.length - 1))]
    readonly property Item currentTargetItem: {
        if (!workspaceItem)
            return null;
        switch (current.targetKey) {
        case "tool": return workspaceItem.tourToolPanel;
        case "preview": return workspaceItem.tourPreviewPanel;
        case "inspector": return workspaceItem.tourInspectorPanel;
        case "timeline": return workspaceItem.tourTimelinePanel;
        default: return null;
        }
    }
    readonly property string cardPlacement: {
        const rightRoom = width - target.x - target.width;
        const leftRoom = target.x;
        const belowRoom = height - target.y - target.height;
        const aboveRoom = target.y;
        if (rightRoom >= card.width + cardGap + 16)
            return "right";
        if (leftRoom >= card.width + cardGap + 16)
            return "left";
        if (belowRoom >= card.height + cardGap + 16)
            return "below";
        if (aboveRoom >= card.height + cardGap + 16)
            return "above";
        return rightRoom >= leftRoom ? "right" : "left";
    }

    function syncTarget() {
        const item = root.currentTargetItem;
        if (!item || !item.visible || item.width <= 0 || item.height <= 0) {
            root.target = Qt.rect(0, 0, 0, 0);
            return;
        }
        const topLeft = item.mapToItem(root, 0, 0);
        const left = Math.max(8, topLeft.x - root.targetPadding);
        const top = Math.max(8, topLeft.y - root.targetPadding);
        const right = Math.min(root.width - 8,
            topLeft.x + item.width + root.targetPadding);
        const bottom = Math.min(root.height - 8,
            topLeft.y + item.height + root.targetPadding);
        root.target = Qt.rect(
            Math.round(left), Math.round(top),
            Math.max(0, Math.round(right - left)),
            Math.max(0, Math.round(bottom - top)));
    }

    function open() {
        step = 0;
        visible = true;
        forceActiveFocus();
        Qt.callLater(syncTarget);
    }
    function finish() {
        visible = false;
        mediaflow.settingsController.setWorkspaceTourCompleted(true);
    }

    onStepChanged: Qt.callLater(syncTarget)
    onWidthChanged: Qt.callLater(syncTarget)
    onHeightChanged: Qt.callLater(syncTarget)
    onWorkspaceItemChanged: Qt.callLater(syncTarget)

    Timer {
        interval: 50
        repeat: true
        running: root.visible
        onTriggered: root.syncTarget()
    }

    Keys.onEscapePressed: root.finish()
    Rectangle { x: 0; y: 0; width: root.width; height: Math.max(0, root.target.y); color: "#a6000000" }
    Rectangle { x: 0; y: root.target.y; width: Math.max(0, root.target.x); height: Math.max(0, root.target.height); color: "#a6000000" }
    Rectangle { x: root.target.x + root.target.width; y: root.target.y; width: Math.max(0, root.width - x); height: Math.max(0, root.target.height); color: "#a6000000" }
    Rectangle { x: 0; y: root.target.y + root.target.height; width: root.width; height: Math.max(0, root.height - y); color: "#a6000000" }
    Rectangle {
        objectName: "workspaceTourHighlight"
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
        objectName: "workspaceTourCard"
        width: Math.min(390, root.width - 32)
        implicitHeight: cardContent.implicitHeight + 24
        x: Math.max(16, Math.min(root.width - width - 16,
            root.cardPlacement === "right"
                ? root.target.x + root.target.width + root.cardGap
                : root.cardPlacement === "left"
                    ? root.target.x - width - root.cardGap
                    : root.target.x + (root.target.width - width) / 2))
        y: Math.max(16, Math.min(root.height - height - 16,
            root.cardPlacement === "below"
                ? root.target.y + root.target.height + root.cardGap
                : root.cardPlacement === "above"
                    ? root.target.y - height - root.cardGap
                    : root.target.y + 12))
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
                    objectName: "workspaceTourNextButton"
                    primary: true
                    text: root.step + 1 === root.steps.length ? qsTr("完成") : qsTr("下一步")
                    onClicked: root.step + 1 === root.steps.length
                        ? root.finish() : root.step += 1
                }
            }
        }
    }
}

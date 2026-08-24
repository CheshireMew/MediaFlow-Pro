import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "."
import "components"

Rectangle {
    id: root

    signal openSettingsRequested()
    signal openExportRequested()

    objectName: "workflowBanner"
    visible: mediaflow.workspaceViewController.workflowPending
        && !(mediaflow.workspaceViewController.workflowStage === "download"
            && mediaflow.downloadController.downloadProgressVisible)
    color: mediaflow.workspaceViewController.workflowStatus === "blocked"
        ? Theme.warningSoft : Theme.surfaceFloating
    radius: Theme.radius
    border.width: 1
    border.color: mediaflow.workspaceViewController.workflowStatus === "blocked"
        ? Theme.warning : Theme.borderStrong
    clip: true
    readonly property bool compactLayout: width < 620

    Rectangle {
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        height: 1
        color: Theme.divider
    }

    readonly property bool canAct:
        mediaflow.workspaceViewController.actionCapabilities.canManageWorkflow
    readonly property bool canSkip:
        ["prepare_media", "transcribe", "translate", "highlight",
         "create_shorts"].indexOf(mediaflow.workspaceViewController.workflowStage) >= 0

    function stageLabel(stage) {
        if (stage === "download") return qsTr("下载")
        if (stage === "prepare_media") return qsTr("媒体分析、代理与波形")
        if (stage === "transcribe") return qsTr("转录")
        if (stage === "translate") return qsTr("翻译")
        if (stage === "highlight") return qsTr("AI 高光分析")
        if (stage === "create_shorts") return qsTr("创建短视频草稿")
        if (stage === "export") return qsTr("导出")
        return qsTr("工作流")
    }

    function stageIcon(stage) {
        if (stage === "download") return "download"
        if (stage === "prepare_media") return "media"
        if (stage === "transcribe") return "transcript"
        if (stage === "translate") return "translate"
        if (stage === "highlight") return "highlight"
        if (stage === "create_shorts") return "cut"
        if (stage === "export") return "export"
        return "tasks"
    }

    function message(code) {
        if (code === "workflow_download_request_required")
            return qsTr("下载计划已失效，请重新分析链接。")
        if (code === "workflow_download_artifacts_missing")
            return qsTr("下载任务没有生成可用文件，请在任务中心查看原因。")
        if (code === "workflow_llm_provider_required")
            return qsTr("需要先配置并启用 LLM 提供商，也可以跳过当前阶段。")
        if (code === "workflow_export_confirmation_required")
            return qsTr("请前往导出页确认设置并开始导出。")
        if (code === "workflow_offline_assets")
            return qsTr("工作流包含离线素材，请先重新关联。")
        if (code === "workflow_task_failed")
            return qsTr("阶段任务失败。查看任务详情后可以恢复或跳过。")
        if (code === "workflow_task_cancelled")
            return qsTr("阶段任务已取消，可以恢复或跳过。")
        if (code === "workflow_interrupted")
            return qsTr("上次运行被中断，可以从当前阶段恢复。")
        if (code.indexOf("_running") >= 0)
            return qsTr("正在执行，进度可在任务中心查看。")
        if (code.indexOf("_ready") >= 0)
            return qsTr("上一阶段已完成，请确认是否继续。")
        return qsTr("工作流已暂停，请处理当前阶段。")
    }

    RowLayout {
        anchors.fill: parent
        anchors.leftMargin: 14
        anchors.rightMargin: 12
        spacing: 8

        Rectangle {
            visible: !root.compactLayout
            Layout.preferredWidth: 3
            Layout.preferredHeight: 32
            radius: 2
            color: mediaflow.workspaceViewController.workflowStatus === "blocked"
                ? Theme.warning : Theme.accent
        }

        AppIcon {
            Layout.preferredWidth: 22
            Layout.preferredHeight: 22
            iconName: root.stageIcon(mediaflow.workspaceViewController.workflowStage)
            iconColor: mediaflow.workspaceViewController.workflowStatus === "blocked"
                ? Theme.warning : Theme.accentHover
            ToolTip.visible: root.compactLayout && workflowIconHover.hovered
            ToolTip.text: root.stageLabel(mediaflow.workspaceViewController.workflowStage)
                + " · " + root.message(mediaflow.workspaceViewController.workflowMessageCode)
            HoverHandler { id: workflowIconHover }
        }

        ColumnLayout {
            Layout.fillWidth: true
            spacing: 2
            Text {
                objectName: "workflowCompactSummary"
                Layout.fillWidth: true
                text: root.compactLayout
                    ? root.stageLabel(mediaflow.workspaceViewController.workflowStage)
                        + " · " + root.message(mediaflow.workspaceViewController.workflowMessageCode)
                    : root.stageLabel(mediaflow.workspaceViewController.workflowStage)
                color: Theme.text
                font.pixelSize: root.compactLayout
                    ? Theme.fontSizeCaption : Theme.fontSizeBody
                font.weight: Font.DemiBold
                elide: Text.ElideRight
                ToolTip.visible: root.compactLayout && compactWorkflowTextHover.hovered
                    && implicitWidth > width
                ToolTip.text: text
                HoverHandler { id: compactWorkflowTextHover }
            }
            Text {
                Layout.fillWidth: true
                visible: !root.compactLayout
                text: root.message(mediaflow.workspaceViewController.workflowMessageCode)
                color: Theme.textMuted
                font.pixelSize: Theme.fontSizeCaption
                elide: Text.ElideRight
                ToolTip.visible: workflowMessageHover.hovered
                    && implicitWidth > width
                ToolTip.text: text
                HoverHandler { id: workflowMessageHover }
            }
        }

        AppComboBox {
            id: workflowLanguage
            objectName: "workflowLanguage"
            visible: mediaflow.workspaceViewController.workflowStage === "translate"
            enabled: root.canAct
            Layout.preferredWidth: root.compactLayout ? 120 : 150
            textRole: "label"
            valueRole: "value"
            model: [
                { label: qsTr("选择目标语言"), value: "" },
                { label: qsTr("中文"), value: "zh_CN" },
                { label: "English", value: "en" },
                { label: qsTr("日本语"), value: "ja" }
            ]
            Component.onCompleted: {
                const wanted = mediaflow.settingsController.defaultTranslationLanguage
                for (let index = 0; index < model.length; ++index) {
                    if (model[index].value === wanted) {
                        currentIndex = index
                        break
                    }
                }
            }
        }

        AppButton {
            objectName: "workflowContinue"
            primary: true
            compact: true
            visible: mediaflow.workspaceViewController.workflowStatus !== "running"
            enabled: root.canAct
                && (mediaflow.workspaceViewController.workflowStage !== "translate"
                    || workflowLanguage.currentValue.length > 0
                    || mediaflow.workspaceViewController.workflowMessageCode
                        === "workflow_llm_provider_required")
            text: mediaflow.workspaceViewController.workflowMessageCode
                    === "workflow_llm_provider_required"
                ? qsTr("打开设置")
                : mediaflow.workspaceViewController.workflowStage === "export"
                ? qsTr("前往导出")
                : mediaflow.workspaceViewController.workflowStatus === "blocked"
                ? qsTr("恢复")
                : qsTr("确认继续")
            onClicked: {
                if (mediaflow.workspaceViewController.workflowMessageCode
                        === "workflow_llm_provider_required")
                    root.openSettingsRequested()
                else if (mediaflow.workspaceViewController.workflowStage === "export")
                    root.openExportRequested()
                else
                    mediaflow.workspaceWorkflowController.continueWorkflow(
                        mediaflow.workspaceViewController.workflowRunId,
                        workflowLanguage.currentValue || "")
            }
        }

        AppButton {
            objectName: "workflowSkip"
            compact: true
            visible: root.canSkip && !root.compactLayout
            enabled: root.canAct
            text: qsTr("跳过")
            onClicked: mediaflow.workspaceWorkflowController.skipWorkflow(
                mediaflow.workspaceViewController.workflowRunId)
        }

        AppButton {
            objectName: "workflowCancel"
            compact: true
            visible: !root.compactLayout
            enabled: root.canAct
            danger: true
            text: qsTr("取消")
            onClicked: mediaflow.workspaceWorkflowController.cancelWorkflow(
                mediaflow.workspaceViewController.workflowRunId)
        }

        AppIconButton {
            id: compactWorkflowMenuButton
            objectName: "workflowCompactMenuButton"
            visible: root.compactLayout
            compact: true
            iconName: "more"
            flat: true
            Accessible.name: qsTr("更多工作流操作")
            toolTipText: Accessible.name
            AppMenu {
                id: compactWorkflowMenu
                y: compactWorkflowMenuButton.height + 4
                AppMenuItem {
                    objectName: "workflowCompactSkip"
                    visible: root.canSkip
                    text: qsTr("跳过当前阶段")
                    enabled: root.canAct
                    onTriggered: mediaflow.workspaceWorkflowController.skipWorkflow(
                        mediaflow.workspaceViewController.workflowRunId)
                }
                AppMenuSeparator { visible: root.canSkip }
                AppMenuItem {
                    text: qsTr("取消工作流")
                    enabled: root.canAct
                    onTriggered: mediaflow.workspaceWorkflowController.cancelWorkflow(
                        mediaflow.workspaceViewController.workflowRunId)
                }
            }
            onClicked: compactWorkflowMenu.open()
        }
    }
}

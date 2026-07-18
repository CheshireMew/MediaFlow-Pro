import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import ".."

Rectangle {
    id: root

    signal openSettingsRequested()
    signal openExportRequested()

    objectName: "workflowBanner"
    visible: workspaceController.workflowPending
             && !(workspaceController.workflowStage === "download"
                  && taskController.downloadProgressVisible)
    color: workspaceController.workflowStatus === "blocked" ? "#352318" : Theme.accentSoft
    border.color: workspaceController.workflowStatus === "blocked" ? Theme.warning : Theme.accent

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

    function message(code) {
        if (code === "workflow_download_request_required") return qsTr("下载计划已失效，请重新分析链接。")
        if (code === "workflow_download_artifacts_missing") return qsTr("下载任务没有生成可用文件，请在任务中心查看原因。")
        if (code === "workflow_llm_provider_required") return qsTr("需要先配置并启用 LLM 提供商。")
        if (code === "workflow_export_confirmation_required") return qsTr("请在导出页确认设置并开始导出。")
        if (code === "workflow_offline_assets") return qsTr("工作流包含离线素材，请先重新关联。")
        if (code === "workflow_task_failed") return qsTr("阶段任务失败，可在任务中心查看原因后重试。")
        if (code === "workflow_task_cancelled") return qsTr("阶段任务已取消，可重新继续。")
        if (code.indexOf("_running") >= 0) return qsTr("正在执行，进度可在任务中心查看。")
        if (code.indexOf("_ready") >= 0) return qsTr("上一阶段已完成，确认后继续。")
        return qsTr("工作流已暂停，请处理当前阶段。")
    }

    RowLayout {
        anchors.fill: parent
        anchors.leftMargin: 66
        anchors.rightMargin: 14
        spacing: 12

        ColumnLayout {
            Layout.fillWidth: true
            spacing: 2
            Text {
                text: root.stageLabel(workspaceController.workflowStage)
                color: Theme.text
                font.pixelSize: Theme.fontSizeBody
                font.weight: Font.DemiBold
            }
            Text {
                Layout.fillWidth: true
                text: root.message(workspaceController.workflowMessageCode)
                color: Theme.textMuted
                font.pixelSize: Theme.fontSizeCaption
                elide: Text.ElideRight
            }
        }
        AppComboBox {
            id: workflowLanguage
            objectName: "workflowLanguage"
            visible: workspaceController.workflowStage === "translate"
            Layout.preferredWidth: 150
            textRole: "label"
            valueRole: "value"
            model: [
                { label: qsTr("选择目标语言"), value: "" },
                { label: qsTr("中文"), value: "zh_CN" },
                { label: "English", value: "en" },
                { label: qsTr("日本语"), value: "ja" }
            ]
            Component.onCompleted: {
                const wanted = settingsController.defaultTranslationLanguage
                for (var index = 0; index < model.length; ++index) {
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
            visible: workspaceController.workflowStatus !== "running"
            enabled: workspaceController.workflowStage !== "translate"
                || workflowLanguage.currentValue.length > 0
            text: workspaceController.workflowMessageCode === "workflow_llm_provider_required"
                ? qsTr("打开设置")
                : workspaceController.workflowStage === "export" ? qsTr("前往导出") : qsTr("继续")
            onClicked: {
                if (workspaceController.workflowMessageCode === "workflow_llm_provider_required")
                    root.openSettingsRequested()
                else if (workspaceController.workflowStage === "export")
                    root.openExportRequested()
                else
                    workspaceController.continueWorkflow(
                        workspaceController.workflowRunId,
                        workflowLanguage.currentValue || "")
            }
        }
        AppButton {
            text: qsTr("取消工作流")
            onClicked: workspaceController.cancelWorkflow(workspaceController.workflowRunId)
        }
    }
}

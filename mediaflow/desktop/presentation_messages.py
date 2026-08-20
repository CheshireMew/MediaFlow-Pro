from __future__ import annotations

from PySide6.QtCore import QCoreApplication


def system_name(name: str) -> str:
    exact = {
        "主序列": QCoreApplication.translate("SystemNameCatalog", "主序列"),
        "主总线": QCoreApplication.translate("SystemNameCatalog", "主总线"),
        "对白": QCoreApplication.translate("SystemNameCatalog", "对白"),
        "音乐": QCoreApplication.translate("SystemNameCatalog", "音乐"),
        "效果": QCoreApplication.translate("SystemNameCatalog", "效果"),
    }
    if name in exact:
        return exact[name]
    prefixes = {
        "短视频 ": QCoreApplication.translate("SystemNameCatalog", "短视频 %1"),
        "视频 ": QCoreApplication.translate("SystemNameCatalog", "视频 %1"),
        "音频 ": QCoreApplication.translate("SystemNameCatalog", "音频 %1"),
        "字幕 ": QCoreApplication.translate("SystemNameCatalog", "字幕 %1"),
    }
    for prefix, template in prefixes.items():
        suffix = name[len(prefix) :] if name.startswith(prefix) else ""
        if suffix.isdigit():
            return template.replace("%1", suffix)
    return name


def status_message(source: str, *arguments: object) -> str:
    templates = {
        "%1 连接测试成功": QCoreApplication.translate("StatusMessageCatalog", "%1 连接测试成功"),
        "Cookie 已保存到 %1": QCoreApplication.translate("StatusMessageCatalog", "Cookie 已保存到 %1"),
        "Cookie 已清除": QCoreApplication.translate("StatusMessageCatalog", "Cookie 已清除"),
        "LLM 提供商已保存": QCoreApplication.translate("StatusMessageCatalog", "LLM 提供商已保存"),
        "LLM 提供商已移除": QCoreApplication.translate("StatusMessageCatalog", "LLM 提供商已移除"),
        "修改已应用到字幕文档": QCoreApplication.translate("StatusMessageCatalog", "修改已应用到字幕文档"),
        "分析期间时间线已修改，请重新运行智能入出点": QCoreApplication.translate(
            "StatusMessageCatalog", "分析期间时间线已修改，请重新运行智能入出点"
        ),
        "场景切点已写入时间线": QCoreApplication.translate("StatusMessageCatalog", "场景切点已写入时间线"),
        "当前 LLM 提供商已切换": QCoreApplication.translate("StatusMessageCatalog", "当前 LLM 提供商已切换"),
        "当前工作流阶段正在运行": QCoreApplication.translate(
            "StatusMessageCatalog", "当前工作流阶段正在运行"
        ),
        "短视频序列已创建": QCoreApplication.translate("StatusMessageCatalog", "短视频序列已创建"),
        "短视频序列已移除；可使用撤销恢复": QCoreApplication.translate(
            "StatusMessageCatalog", "短视频序列已移除；可使用撤销恢复"
        ),
        "工作流任务失败：%1": QCoreApplication.translate("StatusMessageCatalog", "工作流任务失败：%1"),
        "高光候选已保存": QCoreApplication.translate("StatusMessageCatalog", "高光候选已保存"),
        "高光候选已删除": QCoreApplication.translate("StatusMessageCatalog", "高光候选已删除"),
        "高光区间已添加到主序列": QCoreApplication.translate(
            "StatusMessageCatalog", "高光区间已添加到主序列"
        ),
        "默认下载目录已更新": QCoreApplication.translate("StatusMessageCatalog", "默认下载目录已更新"),
        "默认项目保存目录已更新": QCoreApplication.translate(
            "StatusMessageCatalog", "默认项目保存目录已更新"
        ),
        "外部修改与当前输入冲突，已保护未提交内容": QCoreApplication.translate(
            "StatusMessageCatalog", "外部修改与当前输入冲突，已保护未提交内容"
        ),
        "字幕已保存": QCoreApplication.translate("StatusMessageCatalog", "字幕已保存"),
        "诊断包任务已加入任务中心": QCoreApplication.translate(
            "StatusMessageCatalog", "诊断包任务已加入任务中心"
        ),
        "字幕已合并": QCoreApplication.translate("StatusMessageCatalog", "字幕已合并"),
        "字幕已导出到 %1": QCoreApplication.translate("StatusMessageCatalog", "字幕已导出到 %1"),
        "字幕已拆分": QCoreApplication.translate("StatusMessageCatalog", "字幕已拆分"),
        "字幕样式预设已移除": QCoreApplication.translate("StatusMessageCatalog", "字幕样式预设已移除"),
        "已从时间线选区创建短视频序列": QCoreApplication.translate(
            "StatusMessageCatalog", "已从时间线选区创建短视频序列"
        ),
        "已从最近项目中移除": QCoreApplication.translate("StatusMessageCatalog", "已从最近项目中移除"),
        "已从高光创建短视频序列": QCoreApplication.translate(
            "StatusMessageCatalog", "已从高光创建短视频序列"
        ),
        "已保存字幕样式预设：%1": QCoreApplication.translate(
            "StatusMessageCatalog", "已保存字幕样式预设：%1"
        ),
        "已保存序列字幕覆盖": QCoreApplication.translate("StatusMessageCatalog", "已保存序列字幕覆盖"),
        "已保留你的修改": QCoreApplication.translate("StatusMessageCatalog", "已保留你的修改"),
        "已修复 %1 条重叠字幕": QCoreApplication.translate("StatusMessageCatalog", "已修复 %1 条重叠字幕"),
        "已创建 %1 个短视频草稿": QCoreApplication.translate(
            "StatusMessageCatalog", "已创建 %1 个短视频草稿"
        ),
        "已创建命名版本“%1”": QCoreApplication.translate("StatusMessageCatalog", "已创建命名版本“%1”"),
        "已创建复合片段": QCoreApplication.translate("StatusMessageCatalog", "已创建复合片段"),
        "已创建素材文件夹：%1": QCoreApplication.translate("StatusMessageCatalog", "已创建素材文件夹：%1"),
        "已删除 %1 条字幕": QCoreApplication.translate("StatusMessageCatalog", "已删除 %1 条字幕"),
        "已复制 %1 条字幕": QCoreApplication.translate("StatusMessageCatalog", "已复制 %1 条字幕"),
        "已实时同步 %1 的修改": QCoreApplication.translate("StatusMessageCatalog", "已实时同步 %1 的修改"),
        "已导入 %1": QCoreApplication.translate("StatusMessageCatalog", "已导入 %1"),
        "已导入 %1 个素材": QCoreApplication.translate("StatusMessageCatalog", "已导入 %1 个素材"),
        "已导入 %1，共 %2 条字幕": QCoreApplication.translate(
            "StatusMessageCatalog", "已导入 %1，共 %2 条字幕"
        ),
        "已导出 FCPXML：%1": QCoreApplication.translate("StatusMessageCatalog", "已导出 FCPXML：%1"),
        "已将 %1 放入时间轴": QCoreApplication.translate("StatusMessageCatalog", "已将 %1 放入时间轴"),
        "已将当前画面保存为素材：%1": QCoreApplication.translate(
            "StatusMessageCatalog", "已将当前画面保存为素材：%1"
        ),
        "已恢复命名版本“%1”": QCoreApplication.translate("StatusMessageCatalog", "已恢复命名版本“%1”"),
        "已恢复字幕文档时间": QCoreApplication.translate("StatusMessageCatalog", "已恢复字幕文档时间"),
        "已放入 %1 条字幕": QCoreApplication.translate("StatusMessageCatalog", "已放入 %1 条字幕"),
        "已更新 %1 总线": QCoreApplication.translate("StatusMessageCatalog", "已更新 %1 总线"),
        "已替换 %1 处文本": QCoreApplication.translate("StatusMessageCatalog", "已替换 %1 处文本"),
        "已替换 %1 条字幕": QCoreApplication.translate("StatusMessageCatalog", "已替换 %1 条字幕"),
        "已替换当前匹配": QCoreApplication.translate("StatusMessageCatalog", "已替换当前匹配"),
        "已替换素材内容，预览缓存和音频波形将重新生成": QCoreApplication.translate(
            "StatusMessageCatalog", "已替换素材内容，预览缓存和音频波形将重新生成"
        ),
        "已添加字幕": QCoreApplication.translate("StatusMessageCatalog", "已添加字幕"),
        "已添加手动高光候选": QCoreApplication.translate("StatusMessageCatalog", "已添加手动高光候选"),
        "已采用最新项目内容": QCoreApplication.translate("StatusMessageCatalog", "已采用最新项目内容"),
        "已清理 %1 条任务记录，任务产物仍保留": QCoreApplication.translate(
            "StatusMessageCatalog", "已清理 %1 条任务记录，任务产物仍保留"
        ),
        "已设置序列入出点：%1–%2 帧": QCoreApplication.translate(
            "StatusMessageCatalog", "已设置序列入出点：%1–%2 帧"
        ),
        "已设置序列入出点：%1–%2 帧；未发现启用的字幕，只处理了黑屏": QCoreApplication.translate(
            "StatusMessageCatalog", "已设置序列入出点：%1–%2 帧；未发现启用的字幕，只处理了黑屏"
        ),
        "已设置序列入出点：%1–%2 帧；结果已应用到原序列": QCoreApplication.translate(
            "StatusMessageCatalog", "已设置序列入出点：%1–%2 帧；结果已应用到原序列"
        ),
        (
            "已设置序列入出点：%1–%2 帧；未发现启用的字幕，只处理了黑屏；结果已应用到原序列"
        ): QCoreApplication.translate(
            "StatusMessageCatalog",
            "已设置序列入出点：%1–%2 帧；未发现启用的字幕，只处理了黑屏；结果已应用到原序列",
        ),
        "已设置序列入点": QCoreApplication.translate("StatusMessageCatalog", "已设置序列入点"),
        "已设置序列出点": QCoreApplication.translate("StatusMessageCatalog", "已设置序列出点"),
        "已移动序列字幕": QCoreApplication.translate("StatusMessageCatalog", "已移动序列字幕"),
        "已调整序列字幕时间": QCoreApplication.translate("StatusMessageCatalog", "已调整序列字幕时间"),
        "已调整序列入出点": QCoreApplication.translate("StatusMessageCatalog", "已调整序列入出点"),
        "已清除序列入出点": QCoreApplication.translate("StatusMessageCatalog", "已清除序列入出点"),
        "已移除任务记录，任务产物仍保留": QCoreApplication.translate(
            "StatusMessageCatalog", "已移除任务记录，任务产物仍保留"
        ),
        "已解除复合片段": QCoreApplication.translate("StatusMessageCatalog", "已解除复合片段"),
        "已解除视音频绑定；当前仅选中视频。点击空白处或按 Esc 可清除选择": QCoreApplication.translate(
            "StatusMessageCatalog", "已解除视音频绑定；当前仅选中视频。点击空白处或按 Esc 可清除选择"
        ),
        "已请求取消 %1 个任务": QCoreApplication.translate("StatusMessageCatalog", "已请求取消 %1 个任务"),
        "已请求取消任务": QCoreApplication.translate("StatusMessageCatalog", "已请求取消任务"),
        "已请求取消运行时工具操作": QCoreApplication.translate(
            "StatusMessageCatalog", "已请求取消运行时工具操作"
        ),
        "已请求暂停 %1 个任务": QCoreApplication.translate("StatusMessageCatalog", "已请求暂停 %1 个任务"),
        "已请求暂停任务": QCoreApplication.translate("StatusMessageCatalog", "已请求暂停任务"),
        "已选择水印 %1": QCoreApplication.translate("StatusMessageCatalog", "已选择水印 %1"),
        "已重新关联 %1 个素材": QCoreApplication.translate("StatusMessageCatalog", "已重新关联 %1 个素材"),
        "已重新关联 %1 个素材，仍有 %2 个未找到": QCoreApplication.translate(
            "StatusMessageCatalog", "已重新关联 %1 个素材，仍有 %2 个未找到"
        ),
        "已重新创建任务": QCoreApplication.translate("StatusMessageCatalog", "已重新创建任务"),
        "已跳过工作流阶段：%1": QCoreApplication.translate("StatusMessageCatalog", "已跳过工作流阶段：%1"),
        "序列配置已更新": QCoreApplication.translate("StatusMessageCatalog", "序列配置已更新"),
        "智能拆分完成，共拆分 %1 条": QCoreApplication.translate(
            "StatusMessageCatalog", "智能拆分完成，共拆分 %1 条"
        ),
        "术语已保存": QCoreApplication.translate("StatusMessageCatalog", "术语已保存"),
        "术语已移除": QCoreApplication.translate("StatusMessageCatalog", "术语已移除"),
        "正在关闭项目并释放文件…": QCoreApplication.translate(
            "StatusMessageCatalog", "正在关闭项目并释放文件…"
        ),
        "正在分析画面主体": QCoreApplication.translate("StatusMessageCatalog", "正在分析画面主体"),
        "正在导入 %1": QCoreApplication.translate("StatusMessageCatalog", "正在导入 %1"),
        "正在导入 %1 个素材": QCoreApplication.translate("StatusMessageCatalog", "正在导入 %1 个素材"),
        "正在导入水印 %1": QCoreApplication.translate("StatusMessageCatalog", "正在导入水印 %1"),
        "正在检测场景切点": QCoreApplication.translate("StatusMessageCatalog", "正在检测场景切点"),
        "片段素材已替换": QCoreApplication.translate("StatusMessageCatalog", "片段素材已替换"),
        "画面跟踪已应用": QCoreApplication.translate("StatusMessageCatalog", "画面跟踪已应用"),
        "离线素材已重新关联": QCoreApplication.translate("StatusMessageCatalog", "离线素材已重新关联"),
        "素材文件夹已更新": QCoreApplication.translate("StatusMessageCatalog", "素材文件夹已更新"),
        "视觉效果已更新": QCoreApplication.translate("StatusMessageCatalog", "视觉效果已更新"),
        "视觉效果已添加": QCoreApplication.translate("StatusMessageCatalog", "视觉效果已添加"),
        "视觉效果已移除": QCoreApplication.translate("StatusMessageCatalog", "视觉效果已移除"),
        "视觉效果顺序已更新": QCoreApplication.translate("StatusMessageCatalog", "视觉效果顺序已更新"),
        "设置已保存；界面语言将在下次启动时生效": QCoreApplication.translate(
            "StatusMessageCatalog", "设置已保存；界面语言将在下次启动时生效"
        ),
        "示例项目已创建；跟随引导认识主要区域": QCoreApplication.translate(
            "StatusMessageCatalog", "示例项目已创建；跟随引导认识主要区域"
        ),
        "译文已保存": QCoreApplication.translate("StatusMessageCatalog", "译文已保存"),
        "该域名没有已保存的 Cookie": QCoreApplication.translate(
            "StatusMessageCatalog", "该域名没有已保存的 Cookie"
        ),
        "该高光区间已经位于主序列中": QCoreApplication.translate(
            "StatusMessageCatalog", "该高光区间已经位于主序列中"
        ),
        "转场已添加": QCoreApplication.translate("StatusMessageCatalog", "转场已添加"),
        "转录设置已更新": QCoreApplication.translate("StatusMessageCatalog", "转录设置已更新"),
        "运行时工具操作已取消": QCoreApplication.translate("StatusMessageCatalog", "运行时工具操作已取消"),
        "运行时工具操作已完成": QCoreApplication.translate("StatusMessageCatalog", "运行时工具操作已完成"),
        "错误详情已复制": QCoreApplication.translate("StatusMessageCatalog", "错误详情已复制"),
        "项目已保存": QCoreApplication.translate("StatusMessageCatalog", "项目已保存"),
        "项目已关闭：%1": QCoreApplication.translate("StatusMessageCatalog", "项目已关闭：%1"),
        "项目已创建": QCoreApplication.translate("StatusMessageCatalog", "项目已创建"),
        "项目已创建，正在下载视频": QCoreApplication.translate(
            "StatusMessageCatalog", "项目已创建，正在下载视频"
        ),
        "项目已创建，正在下载音频": QCoreApplication.translate(
            "StatusMessageCatalog", "项目已创建，正在下载音频"
        ),
        "项目已打开": QCoreApplication.translate("StatusMessageCatalog", "项目已打开"),
        "项目正被其他窗口使用，已只读打开": QCoreApplication.translate(
            "StatusMessageCatalog", "项目正被其他窗口使用，已只读打开"
        ),
    }
    try:
        result = templates[source]
    except KeyError as error:
        raise ValueError(f"Unregistered status message source: {source}") from error
    for index, argument in enumerate(arguments, start=1):
        result = result.replace(f"%{index}", str(argument))
    return result

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
        "已安排在下次启动时迁移并切换运行环境目录": QCoreApplication.translate(
            "StatusMessageCatalog", "已安排在下次启动时迁移并切换运行环境目录"
        ),
        "已安排在下次启动时切换运行环境目录": QCoreApplication.translate(
            "StatusMessageCatalog", "已安排在下次启动时切换运行环境目录"
        ),
        "已取消运行环境目录变更": QCoreApplication.translate(
            "StatusMessageCatalog", "已取消运行环境目录变更"
        ),
        "时间线标记已添加": QCoreApplication.translate("StatusMessageCatalog", "时间线标记已添加"),
        "时间线标记已重命名": QCoreApplication.translate("StatusMessageCatalog", "时间线标记已重命名"),
        "时间线标记已删除；可使用撤销恢复": QCoreApplication.translate(
            "StatusMessageCatalog", "时间线标记已删除；可使用撤销恢复"
        ),
        "时间线选区已添加": QCoreApplication.translate("StatusMessageCatalog", "时间线选区已添加"),
        "时间线选区已重命名": QCoreApplication.translate("StatusMessageCatalog", "时间线选区已重命名"),
        "时间线选区已删除；可使用撤销恢复": QCoreApplication.translate(
            "StatusMessageCatalog", "时间线选区已删除；可使用撤销恢复"
        ),
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
        "已新建 %1 个、同步 %2 个交付版本": QCoreApplication.translate(
            "StatusMessageCatalog", "已新建 %1 个、同步 %2 个交付版本"
        ),
        "交付版本变更计划已生成，%1 个冲突": QCoreApplication.translate(
            "StatusMessageCatalog", "交付版本变更计划已生成，%1 个冲突"
        ),
        "%1 个交付版本已是最新，无需重复生成": QCoreApplication.translate(
            "StatusMessageCatalog", "%1 个交付版本已是最新，无需重复生成"
        ),
        "已归集 %1 个外部素材，共 %2 字节": QCoreApplication.translate(
            "StatusMessageCatalog", "已归集 %1 个外部素材，共 %2 字节"
        ),
        "已使用归集副本": QCoreApplication.translate(
            "StatusMessageCatalog", "已使用归集副本"
        ),
        "已恢复归集前的素材路径": QCoreApplication.translate(
            "StatusMessageCatalog", "已恢复归集前的素材路径"
        ),
        "可迁移项目已归档：%1（%2 个文件）": QCoreApplication.translate(
            "StatusMessageCatalog", "可迁移项目已归档：%1（%2 个文件）"
        ),
        "多机位节目轨已创建": QCoreApplication.translate(
            "StatusMessageCatalog", "多机位节目轨已创建"
        ),
        "多机位角度已切换": QCoreApplication.translate(
            "StatusMessageCatalog", "多机位角度已切换"
        ),
        "多机位同步分析完成，置信度 %1%": QCoreApplication.translate(
            "StatusMessageCatalog", "多机位同步分析完成，置信度 %1%"
        ),
        "已导入 %1 条审阅批注": QCoreApplication.translate(
            "StatusMessageCatalog", "已导入 %1 条审阅批注"
        ),
        "审阅包已导出：%1": QCoreApplication.translate(
            "StatusMessageCatalog", "审阅包已导出：%1"
        ),
        "审阅截图已保存": QCoreApplication.translate(
            "StatusMessageCatalog", "审阅截图已保存"
        ),
        "审阅截图标注已保存": QCoreApplication.translate(
            "StatusMessageCatalog", "审阅截图标注已保存"
        ),
        "贝塞尔曲线已更新": QCoreApplication.translate(
            "StatusMessageCatalog", "贝塞尔曲线已更新"
        ),
        "画面关键帧曲线点已更新": QCoreApplication.translate(
            "StatusMessageCatalog", "画面关键帧曲线点已更新"
        ),
        "蒙版顺序已更新": QCoreApplication.translate(
            "StatusMessageCatalog", "蒙版顺序已更新"
        ),
        "视频示波器已更新": QCoreApplication.translate(
            "StatusMessageCatalog", "视频示波器已更新"
        ),
        "旁白 / ADR 提示已创建": QCoreApplication.translate(
            "StatusMessageCatalog", "旁白 / ADR 提示已创建"
        ),
        "旁白 / ADR 提示已更新": QCoreApplication.translate(
            "StatusMessageCatalog", "旁白 / ADR 提示已更新"
        ),
        "旁白 / ADR 提示已归档": QCoreApplication.translate(
            "StatusMessageCatalog", "旁白 / ADR 提示已归档"
        ),
        "旁白 take 已导入": QCoreApplication.translate(
            "StatusMessageCatalog", "旁白 take 已导入"
        ),
        "旁白 take 已更新": QCoreApplication.translate(
            "StatusMessageCatalog", "旁白 take 已更新"
        ),
        "已选择旁白 take": QCoreApplication.translate(
            "StatusMessageCatalog", "已选择旁白 take"
        ),
        "旁白 take 已归档": QCoreApplication.translate(
            "StatusMessageCatalog", "旁白 take 已归档"
        ),
        "选中的旁白 take 已放入时间线": QCoreApplication.translate(
            "StatusMessageCatalog", "选中的旁白 take 已放入时间线"
        ),
        "旁白录音已开始": QCoreApplication.translate(
            "StatusMessageCatalog", "旁白录音已开始"
        ),
        "旁白录音已保存为新 take": QCoreApplication.translate(
            "StatusMessageCatalog", "旁白录音已保存为新 take"
        ),
        "录音延迟已设为 %1 毫秒": QCoreApplication.translate(
            "StatusMessageCatalog", "录音延迟已设为 %1 毫秒"
        ),
        "延迟校准中：扬声器将播放一段短测试声": QCoreApplication.translate(
            "StatusMessageCatalog", "延迟校准中：扬声器将播放一段短测试声"
        ),
        "延迟校准完成：%1 毫秒，可信度 %2%": QCoreApplication.translate(
            "StatusMessageCatalog", "延迟校准完成：%1 毫秒，可信度 %2%"
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
        "交换时间线已导入：%1": QCoreApplication.translate(
            "StatusMessageCatalog", "交换时间线已导入：%1"
        ),
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
        "画面关键帧已保存": QCoreApplication.translate("StatusMessageCatalog", "画面关键帧已保存"),
        "画面关键帧已移除": QCoreApplication.translate("StatusMessageCatalog", "画面关键帧已移除"),
        "画面关键帧已移动": QCoreApplication.translate("StatusMessageCatalog", "画面关键帧已移动"),
        "画面关键帧时间已缩放": QCoreApplication.translate(
            "StatusMessageCatalog", "画面关键帧时间已缩放"
        ),
        "已复制 %1 个画面关键帧": QCoreApplication.translate(
            "StatusMessageCatalog", "已复制 %1 个画面关键帧"
        ),
        "已粘贴画面关键帧": QCoreApplication.translate(
            "StatusMessageCatalog", "已粘贴画面关键帧"
        ),
        "视觉效果关键帧已保存": QCoreApplication.translate(
            "StatusMessageCatalog", "视觉效果关键帧已保存"
        ),
        "蒙版已添加": QCoreApplication.translate("StatusMessageCatalog", "蒙版已添加"),
        "蒙版已更新": QCoreApplication.translate("StatusMessageCatalog", "蒙版已更新"),
        "蒙版已移除": QCoreApplication.translate("StatusMessageCatalog", "蒙版已移除"),
        "效果蒙版已更新": QCoreApplication.translate(
            "StatusMessageCatalog", "效果蒙版已更新"
        ),
        "正在跟踪蒙版": QCoreApplication.translate("StatusMessageCatalog", "正在跟踪蒙版"),
        "蒙版跟踪已应用": QCoreApplication.translate(
            "StatusMessageCatalog", "蒙版跟踪已应用"
        ),
        "审阅批注已添加": QCoreApplication.translate("StatusMessageCatalog", "审阅批注已添加"),
        "审阅回复已添加": QCoreApplication.translate("StatusMessageCatalog", "审阅回复已添加"),
        "审阅批注已解决": QCoreApplication.translate("StatusMessageCatalog", "审阅批注已解决"),
        "审阅批注已重新打开": QCoreApplication.translate(
            "StatusMessageCatalog", "审阅批注已重新打开"
        ),
        "审阅批注已归档，可随时恢复": QCoreApplication.translate(
            "StatusMessageCatalog", "审阅批注已归档，可随时恢复"
        ),
        "审阅批注已恢复": QCoreApplication.translate("StatusMessageCatalog", "审阅批注已恢复"),
        "蒙版关键帧已保存": QCoreApplication.translate(
            "StatusMessageCatalog", "蒙版关键帧已保存"
        ),
        "蒙版关键帧已移除": QCoreApplication.translate(
            "StatusMessageCatalog", "蒙版关键帧已移除"
        ),
        "蒙版关键帧已移动": QCoreApplication.translate(
            "StatusMessageCatalog", "蒙版关键帧已移动"
        ),
        "蒙版关键帧时间已缩放": QCoreApplication.translate(
            "StatusMessageCatalog", "蒙版关键帧时间已缩放"
        ),
        "视觉效果关键帧已移除": QCoreApplication.translate(
            "StatusMessageCatalog", "视觉效果关键帧已移除"
        ),
        "视觉效果关键帧已移动": QCoreApplication.translate(
            "StatusMessageCatalog", "视觉效果关键帧已移动"
        ),
        "视觉效果关键帧时间已缩放": QCoreApplication.translate(
            "StatusMessageCatalog", "视觉效果关键帧时间已缩放"
        ),
        "已复制 %1 个视觉效果关键帧": QCoreApplication.translate(
            "StatusMessageCatalog", "已复制 %1 个视觉效果关键帧"
        ),
        "已粘贴视觉效果关键帧": QCoreApplication.translate(
            "StatusMessageCatalog", "已粘贴视觉效果关键帧"
        ),
        "离线素材已重新关联": QCoreApplication.translate("StatusMessageCatalog", "离线素材已重新关联"),
        "素材文件夹已更新": QCoreApplication.translate("StatusMessageCatalog", "素材文件夹已更新"),
        "视觉效果已更新": QCoreApplication.translate("StatusMessageCatalog", "视觉效果已更新"),
        "视觉效果已添加": QCoreApplication.translate("StatusMessageCatalog", "视觉效果已添加"),
        "视觉效果已从资源库添加": QCoreApplication.translate(
            "StatusMessageCatalog", "视觉效果已从资源库添加"
        ),
        "LUT 已从资源库添加": QCoreApplication.translate("StatusMessageCatalog", "LUT 已从资源库添加"),
        "音频效果已从资源库添加": QCoreApplication.translate(
            "StatusMessageCatalog", "音频效果已从资源库添加"
        ),
        "已收藏资源": QCoreApplication.translate("StatusMessageCatalog", "已收藏资源"),
        "已取消收藏资源": QCoreApplication.translate("StatusMessageCatalog", "已取消收藏资源"),
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
        "转场已从资源库添加": QCoreApplication.translate("StatusMessageCatalog", "转场已从资源库添加"),
        "转录设置已更新": QCoreApplication.translate("StatusMessageCatalog", "转录设置已更新"),
        "运行时工具操作已取消": QCoreApplication.translate("StatusMessageCatalog", "运行时工具操作已取消"),
        "运行时工具操作已完成": QCoreApplication.translate("StatusMessageCatalog", "运行时工具操作已完成"),
        "错误详情已复制": QCoreApplication.translate("StatusMessageCatalog", "错误详情已复制"),
        "项目已关闭：%1": QCoreApplication.translate("StatusMessageCatalog", "项目已关闭：%1"),
        "项目已创建": QCoreApplication.translate("StatusMessageCatalog", "项目已创建"),
        "项目已创建，正在下载视频": QCoreApplication.translate(
            "StatusMessageCatalog", "项目已创建，正在下载视频"
        ),
        "项目已创建，正在下载音频": QCoreApplication.translate(
            "StatusMessageCatalog", "项目已创建，正在下载音频"
        ),
        "项目已打开": QCoreApplication.translate("StatusMessageCatalog", "项目已打开"),
        "项目已重命名为“%1”": QCoreApplication.translate(
            "StatusMessageCatalog", "项目已重命名为“%1”"
        ),
        "项目路径已复制": QCoreApplication.translate("StatusMessageCatalog", "项目路径已复制"),
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

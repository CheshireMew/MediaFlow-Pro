# ruff: noqa: E501
from __future__ import annotations

import html
import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class BuiltinWebTemplate:
    component_id: str
    name: str
    category: str
    description: str
    title: str
    subtitle: str
    badge: str
    accent: str
    background: str
    surface: str
    tags: tuple[str, ...]
    style: str = "editorial"


BUILTIN_WEB_TEMPLATES: tuple[BuiltinWebTemplate, ...] = (
    BuiltinWebTemplate("minimal-title", "极简主标题", "标题", "适合片头和章节开场的留白标题。", "把重点说清楚", "克制的版式，让内容先被看见", "TITLE 01", "#7c5cff", "#f3f0e9", "#ffffff", ("片头", "极简", "标题")),
    BuiltinWebTemplate("kinetic-quote", "动态引语", "标题", "强调一句关键观点或人物原话。", "好内容，值得停下来读", "一句话建立节奏与记忆点", "QUOTE", "#ff5c35", "#151515", "#242424", ("引语", "观点", "动态"), "dark"),
    BuiltinWebTemplate("chapter-opener", "章节页", "标题", "课程、访谈和长视频的章节分隔。", "第二章", "从问题出发，找到真正的原因", "02 / 06", "#2c6bed", "#eaf0ff", "#ffffff", ("章节", "课程", "长视频")),
    BuiltinWebTemplate("clean-lower-third", "简洁人物条", "下三分之一", "人物姓名与身份信息，适合访谈。", "林晓", "产品设计师 · MediaFlow Pro", "INTERVIEW", "#2f7d67", "#edf4f1", "#ffffff", ("人名条", "访谈", "身份"), "lower"),
    BuiltinWebTemplate("live-lower-third", "直播信息条", "下三分之一", "直播、播客和新闻场景的信息条。", "正在直播", "上海 · 现场连线", "LIVE", "#ff3158", "#101218", "#1b1e27", ("直播", "新闻", "播客"), "dark"),
    BuiltinWebTemplate("number-card", "核心数字卡", "数据", "突出一个关键数字及其解释。", "68%", "用户在前三秒决定是否继续观看", "KEY METRIC", "#00a884", "#e8f7f3", "#ffffff", ("数据", "数字", "指标"), "metric"),
    BuiltinWebTemplate("comparison-card", "对比卡片", "数据", "展示前后、方案或观点的清晰对比。", "更快，也更稳", "处理时间 -42% · 错误率 -31%", "BEFORE / AFTER", "#7c5cff", "#f4f1ff", "#ffffff", ("对比", "数据", "结论"), "split"),
    BuiltinWebTemplate("three-steps", "三步说明", "数据", "快速展示流程、方法或教程步骤。", "三步完成", "导入素材 · 编辑内容 · 导出成片", "HOW IT WORKS", "#ff8a00", "#fff4e5", "#ffffff", ("步骤", "教程", "流程"), "steps"),
    BuiltinWebTemplate("insight-callout", "洞察提示", "故事", "在叙事中插入关键解释或提醒。", "真正的瓶颈不在剪辑", "而在素材、文本和版本之间反复切换", "INSIGHT", "#3267e3", "#eef3ff", "#ffffff", ("洞察", "提示", "解释")),
    BuiltinWebTemplate("timeline-moment", "时间节点", "故事", "回顾事件、产品历史或成长路径。", "2026.07", "一个重要节点，从这里开始", "TIMELINE", "#d14b8f", "#fff0f7", "#ffffff", ("时间线", "历史", "节点"), "timeline"),
    BuiltinWebTemplate("podcast-card", "播客金句", "社交", "适配播客切片和语音内容的金句卡。", "“把复杂的事讲明白”", "EP. 24 · 设计与工具", "PODCAST", "#16a0ff", "#0b1724", "#13283c", ("播客", "金句", "音频"), "dark"),
    BuiltinWebTemplate("subscribe-prompt", "关注提示", "社交", "克制的关注、订阅和行动提示。", "继续一起创作", "关注账号，获得下一期完整拆解", "FOLLOW", "#ef476f", "#fff0f3", "#ffffff", ("关注", "订阅", "行动"), "social"),
    BuiltinWebTemplate("product-feature", "产品亮点", "商业", "展示产品卖点、更新或功能发布。", "一次编辑，多端适配", "横屏、竖屏和方形布局保持可编辑", "NEW FEATURE", "#5a67ff", "#eef0ff", "#ffffff", ("产品", "功能", "发布"), "product"),
    BuiltinWebTemplate("event-countdown", "活动倒计时", "商业", "活动预告、发布会与直播预约。", "还有 03 天", "夏季新品发布会 · 20:00", "SAVE THE DATE", "#ff6b2c", "#fff1e9", "#ffffff", ("活动", "倒计时", "预告"), "event"),
    BuiltinWebTemplate("end-screen", "片尾信息页", "商业", "片尾总结、下一步行动和品牌落版。", "感谢观看", "下一期：如何建立稳定的内容工作流", "SEE YOU NEXT TIME", "#6c63ff", "#15151d", "#22222d", ("片尾", "品牌", "下一期"), "dark"),
)


def materialize_builtin_web_templates(root: Path) -> None:
    """Materialize the built-in catalog into the normal editable-media library boundary."""
    for template in BUILTIN_WEB_TEMPLATES:
        package = root / template.component_id / "builtin-v1"
        package.mkdir(parents=True, exist_ok=True)
        (package / "editable-media.json").write_text(
            json.dumps(_manifest(template), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (package / "index.html").write_text(_html(template), encoding="utf-8")


def _manifest(template: BuiltinWebTemplate) -> dict:
    editable = [
        "content", "color", "font_family", "font_size", "x", "y", "width",
        "height", "rotation", "opacity", "z_index", "visible", "enter_ms",
        "exit_ms", "delay_ms", "duration_ms",
    ]
    layers = [
        ("badge", "眉题", 92, 88, 820, 48, 30),
        ("title", "主标题", 92, 190, 1370, 210, 92),
        ("subtitle", "副标题", 96, 445, 1200, 100, 38),
        ("action", "辅助标签", 96, 760, 660, 82, 26),
    ]
    return {
        "protocol": "editable-media",
        "version": 1,
        "entry": "index.html",
        "canvas": {
            "width": 1920,
            "height": 1080,
            "background_mode": "opaque",
            "background_color": template.background,
        },
        "timeline": {"duration_ms": 6000, "fps": 30, "loop": "repeat"},
        "component": {
            "id": template.component_id,
            "name": template.name,
            "category": template.category,
            "tags": list(template.tags),
            "description": template.description,
            "preview_background": template.background,
            "preview_accent": template.accent,
            "aspect_ratios": ["16:9", "9:16", "1:1"],
        },
        "layers": [
            {
                "id": layer_id,
                "name": name,
                "kind": "text",
                "selector": f"[data-editable-id='{layer_id}']",
                "default_bounds": {"x": x, "y": y, "width": width, "height": height},
                "editable": editable,
                "constraints": {"font_size": {"minimum": 12, "maximum": 180, "step": 1}},
            }
            for layer_id, name, x, y, width, height, _font_size in layers
        ],
        "theme_variables": [
            {"id": "background", "name": "背景", "kind": "color", "css_variable": "--background", "default": template.background},
            {"id": "surface", "name": "表面", "kind": "color", "css_variable": "--surface", "default": template.surface},
            {"id": "accent", "name": "强调色", "kind": "color", "css_variable": "--accent", "default": template.accent},
            {"id": "text", "name": "文字", "kind": "color", "css_variable": "--text", "default": "#f7f7fa" if template.style == "dark" else "#17202b"},
        ],
        "layouts": [
            _layout("landscape", "横屏", 1920, 1080, 92, 88, 1370, 210, 92),
            _layout("portrait", "竖屏", 1080, 1920, 64, 150, 900, 310, 70),
            _layout("square", "方形", 1080, 1080, 64, 90, 900, 250, 70),
        ],
        "default_layout_id": "landscape",
    }


def _layout(
    layout_id: str,
    name: str,
    width: int,
    height: int,
    left: int,
    top: int,
    title_width: int,
    title_height: int,
    badge_top: int,
) -> dict:
    return {
        "id": layout_id,
        "name": name,
        "canvas": {
            "width": width,
            "height": height,
            "background_mode": "opaque",
            "background_color": "#000000",
        },
        "layers": {
            "badge": {"x": left, "y": badge_top, "width": min(820, width - left * 2), "height": 52},
            "title": {"x": left, "y": top + 100, "width": min(title_width, width - left * 2), "height": title_height},
            "subtitle": {"x": left, "y": top + 360, "width": min(1200, width - left * 2), "height": 110},
            "action": {"x": left, "y": height - 210, "width": min(680, width - left * 2), "height": 84},
        },
    }


def _html(template: BuiltinWebTemplate) -> str:
    title = html.escape(template.title)
    subtitle = html.escape(template.subtitle)
    badge = html.escape(template.badge)
    action = html.escape("MEDIAFLOW · 可编辑模板")
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<style>
:root{{--background:{template.background};--surface:{template.surface};--accent:{template.accent};--text:{'#f7f7fa' if template.style == 'dark' else '#17202b'};font-family:Inter,'Segoe UI','Microsoft YaHei',sans-serif}}
*{{box-sizing:border-box}}html,body{{margin:0;width:100%;height:100%;overflow:hidden}}body{{background:var(--background);color:var(--text)}}
.canvas{{position:relative;width:100vw;height:100vh;overflow:hidden;background:radial-gradient(circle at 82% 20%,color-mix(in srgb,var(--accent) 28%,transparent),transparent 28%),var(--background)}}
.canvas:before{{content:'';position:absolute;width:520px;height:520px;right:8%;bottom:-18%;border:2px solid color-mix(in srgb,var(--accent) 48%,transparent);border-radius:50%}}
.rail{{position:absolute;left:0;top:0;width:18px;height:100%;background:var(--accent)}}
.badge,.title,.subtitle,.action{{position:absolute;margin:0}}
.badge{{left:4.8%;top:8.2%;font-size:30px;line-height:1.2;font-weight:800;letter-spacing:.16em;color:var(--accent)}}
.title{{left:4.8%;top:17.6%;width:72%;font-size:92px;line-height:1.02;letter-spacing:-.045em;font-weight:820}}
.subtitle{{left:5%;top:42%;width:64%;font-size:38px;line-height:1.4;color:color-mix(in srgb,var(--text) 66%,transparent)}}
.action{{left:5%;bottom:13%;padding:22px 30px;border:1px solid color-mix(in srgb,var(--accent) 45%,transparent);border-radius:999px;background:color-mix(in srgb,var(--surface) 82%,transparent);font-size:26px;font-weight:700;letter-spacing:.05em;box-shadow:0 22px 70px rgb(0 0 0 / 10%)}}
.pulse{{position:absolute;right:12%;top:30%;width:190px;height:190px;border-radius:40px;background:var(--accent);transform:rotate(18deg);opacity:calc(.72 + var(--pulse,0)*.22)}}
</style></head><body><main class="canvas" data-style="{template.style}"><div class="rail"></div><div class="pulse"></div>
<p class="badge" data-editable-id="badge">{badge}</p><h1 class="title" data-editable-id="title">{title}</h1>
<p class="subtitle" data-editable-id="subtitle">{subtitle}</p><p class="action" data-editable-id="action">{action}</p></main>
<script>
(()=>{{const nodes=Object.fromEntries([...document.querySelectorAll('[data-editable-id]')].map(n=>[n.dataset.editableId,n]));let state={{layers:{{}},animations:{{}},theme:{{}},theme_bindings:{{}},data:{{}},layout:{{}},locks:{{}},revision:0}};let time=0;
const clone=v=>JSON.parse(JSON.stringify(v));const apply=()=>{{for(const [k,v] of Object.entries(state.theme||{{}})){{const css=(state.theme_bindings||{{}})[k];if(css)document.documentElement.style.setProperty(css,String(v))}}for(const [id,node] of Object.entries(nodes)){{const v=(state.layers||{{}})[id]||{{}};if(v.content!==undefined)node.textContent=v.content;if(v.color!==undefined)node.style.color=v.color;if(v.font_family!==undefined)node.style.fontFamily=v.font_family;if(v.font_size!==undefined)node.style.fontSize=v.font_size+'px';if(v.x!==undefined)node.style.left=v.x+'px';if(v.y!==undefined)node.style.top=v.y+'px';if(v.width!==undefined)node.style.width=v.width+'px';if(v.height!==undefined)node.style.height=v.height+'px';node.style.opacity=String(v.opacity??1);node.style.rotate=(v.rotation||0)+'deg';node.style.visibility=(v.visible??true)&&time>=(v.enter_ms??0)&&time<=(v.exit_ms??6000)?'visible':'hidden'}}document.documentElement.style.setProperty('--pulse',String((Math.sin(time/500)+1)/2))}};
window.editableMedia={{ready:Promise.resolve(),getState:()=>clone(state),setState:v=>{{state=clone(v||state);apply();return clone(state)}},setTime:v=>{{time=Math.max(0,Number(v)||0);apply();return time}},getBounds:()=>Object.fromEntries(Object.entries(nodes).map(([id,n])=>{{const r=n.getBoundingClientRect();return[id,{{x:r.x,y:r.y,width:r.width,height:r.height,rotation:0}}]}})),setEditMode:()=>false,setEditCapabilities:()=>{{}},selectLayer:()=>{{}}}};apply()}})();
</script></body></html>"""

# ruff: noqa: E501

from __future__ import annotations

import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

TRANSLATIONS: dict[str, tuple[str, str]] = {
    "默认导入目录（可选）": (
        "Default import directory (optional)",
        "既定の読み込み先（任意）",
    ),
    "默认翻译语言": ("Default translation language", "既定の翻訳言語"),
    "参数预设": ("Parameter Preset", "パラメータープリセット"),
    "默认": ("Default", "デフォルト"),
    "对白": ("Dialogue", "台詞"),
    "轻柔": ("Gentle", "ソフト"),
    "强力": ("Strong", "強力"),
    "社交平台": ("Social", "ソーシャル"),
    "网络视频": ("Web Video", "ウェブ動画"),
    "广播": ("Broadcast", "放送"),
    "移除效果": ("Remove Effect", "エフェクトを削除"),
    "选区转短视频": ("Create Short from Range", "範囲からショート動画を作成"),
    "批量创建全部短视频草稿": (
        "Create Drafts from All Highlights",
        "すべてのハイライトから下書きを作成",
    ),
    "预览": ("Preview", "プレビュー"),
    "添加到主序列": ("Add to Main Sequence", "メインシーケンスに追加"),
    "确认下载": ("Confirm Download", "ダウンロードの確認"),
    "已完成链接分析": ("Link analysis complete", "リンク解析が完了しました"),
    "播放列表 · %1 项 · %2": (
        "Playlist · %1 items · %2",
        "プレイリスト · %1件 · %2",
    ),
    "单个视频 · %1": ("Single video · %1", "単一動画 · %1"),
    "最佳可用质量": ("Best available quality", "利用可能な最高画質"),
    "播放列表项目，例如 1-5,8（留空为全部）": (
        "Playlist items, e.g. 1-5,8 (leave blank for all)",
        "プレイリスト項目（例：1-5,8、空欄ですべて）",
    ),
    "开始下载": ("Start Download", "ダウンロードを開始"),
    "分割片段": ("Split Clip", "クリップを分割"),
    "删除片段": ("Delete Clip", "クリップを削除"),
    "撤销": ("Undo", "元に戻す"),
    "重做": ("Redo", "やり直す"),
    "上一帧": ("Previous Frame", "前のフレーム"),
    "播放": ("Play", "再生"),
    "停止并回到开头": ("Stop and Return to Start", "停止して先頭に戻る"),
    "项目 %1": ("Project %1", "プロジェクト %1"),
    "片段 %1，起始帧 %2，持续 %3 帧": (
        "Clip %1, starts at frame %2, duration %3 frames",
        "クリップ %1、開始フレーム %2、長さ %3 フレーム",
    ),
    "转场 %1，持续 %2 帧": (
        "Transition %1, duration %2 frames",
        "トランジション %1、長さ %2 フレーム",
    ),
    "标记 %1，位于第 %2 帧": (
        "Marker %1 at frame %2",
        "マーカー %1、フレーム %2",
    ),
    "任务与提醒": ("Tasks & Alerts", "タスクと通知"),
    "运行 %1": ("Running %1", "実行中 %1"),
    "失败 %1": ("Failed %1", "失敗 %1"),
    "离线素材 %1": ("Offline media %1", "オフラインメディア %1"),
    "待确认 %1": ("Awaiting confirmation %1", "確認待ち %1"),
    "最近产物 %1": ("Recent outputs %1", "最近の成果物 %1"),
    "最近产物": ("Recent Output", "最近の成果物"),
    "运行 %1 · 失败 %2 · 离线 %3 · 待确认 %4": (
        "Running %1 · Failed %2 · Offline %3 · Awaiting %4",
        "実行中 %1 · 失敗 %2 · オフライン %3 · 確認待ち %4",
    ),
    "Profile（可选）": ("Profile (optional)", "Profile（任意）"),
    "Level（可选）": ("Level (optional)", "Level（任意）"),
    "缩放：Lanczos": ("Scaling: Lanczos", "スケーリング：Lanczos"),
    "缩放：双三次": ("Scaling: Bicubic", "スケーリング：バイキュービック"),
    "缩放：双线性": ("Scaling: Bilinear", "スケーリング：バイリニア"),
    "烧录字幕（其余启用轨道仍导出 SRT）": (
        "Burn-in subtitle (other enabled tracks still export as SRT)",
        "字幕を焼き付け（他の有効なトラックはSRTでも出力）",
    ),
    "M4A 音频 (*.m4a)": ("M4A Audio (*.m4a)", "M4A音声 (*.m4a)"),
    "OGG 音频 (*.ogg)": ("OGG Audio (*.ogg)", "OGG音声 (*.ogg)"),
    "WAV 音频 (*.wav)": ("WAV Audio (*.wav)", "WAV音声 (*.wav)"),
    "序列字幕": ("Sequence Subtitles", "シーケンス字幕"),
    "序列覆盖": ("Sequence override", "シーケンス上書き"),
    "保存为序列覆盖": ("Save as Sequence Override", "シーケンス上書きとして保存"),
    "应用到源文档": ("Apply to Source Document", "ソース文書に適用"),
    "新音频总线名称": ("New audio bus name", "新しいオーディオバス名"),
    "添加总线": ("Add Bus", "バスを追加"),
    "输出到": ("Output to", "出力先"),
    "复制": ("Duplicate", "複製"),
    "已保存": ("Saved", "保存済み"),
    "标记": ("Marker", "マーカー"),
    "设入点": ("Set In", "イン点を設定"),
    "设出点": ("Set Out", "アウト点を設定"),
    "入点 %1": ("In %1", "イン %1"),
    " 帧": (" frames", " フレーム"),
    "帧": ("frames", "フレーム"),
    "调整所选转场": ("Edit Selected Transition", "選択したトランジションを編集"),
    "应用": ("Apply", "適用"),
    "移除转场": ("Remove Transition", "トランジションを削除"),
    "主序列": ("Main Sequence", "メインシーケンス"),
    "高光": ("Highlights", "ハイライト"),
    "编辑": ("Edit", "編集"),
    "音频": ("Audio", "音声"),
    "主": ("Main", "メイン"),
    "短": ("Short", "ショート"),
    "专业音频": ("Professional Audio", "プロオーディオ"),
    "48 kHz 浮点总线图": ("48 kHz floating-point bus graph", "48 kHz 浮動小数点バスグラフ"),
    "序列响度": ("Sequence Loudness", "シーケンスラウドネス"),
    "目标 %1 LUFS / %2 dBTP": ("Target %1 LUFS / %2 dBTP", "目標 %1 LUFS / %2 dBTP"),
    "测量中…": ("Measuring…", "測定中…"),
    "重新测量": ("Measure Again", "再測定"),
    "Peak": ("Peak", "Peak"),
    "True Peak": ("True Peak", "True Peak"),
    "短期（最高）": ("Short-term (Max)", "短期（最大）"),
    "综合响度": ("Integrated Loudness", "統合ラウドネス"),
    "True Peak 上限": ("True Peak Ceiling", "True Peak上限"),
    "上限": ("Ceiling", "上限"),
    "中低频增益": ("Low-Mid Gain", "中低域ゲイン"),
    "中高频增益": ("High-Mid Gain", "中高域ゲイン"),
    "低通": ("Low-pass", "ローパス"),
    "低频增益": ("Low Gain", "低域ゲイン"),
    "高频增益": ("High Gain", "高域ゲイン"),
    "截止频率": ("Cutoff Frequency", "カットオフ周波数"),
    "阈值": ("Threshold", "しきい値"),
    "压缩比": ("Ratio", "レシオ"),
    "启动时间": ("Attack", "アタック"),
    "释放时间": ("Release", "リリース"),
    "混合": ("Mix", "ミックス"),
    "目标响度": ("Target Loudness", "目標ラウドネス"),
    "衰减量": ("Reduction", "減衰量"),
    "驱动总线": ("Driver Bus", "ドライバーバス"),
    "取消静音": ("Unmute", "ミュート解除"),
    "取消独奏": ("Unsolo", "ソロ解除"),
    "静音": ("Mute", "ミュート"),
    "独奏": ("Solo", "ソロ"),
    "启用轨道": ("Enable Track", "トラックを有効化"),
    "禁用轨道": ("Disable Track", "トラックを無効化"),
    "锁定轨道": ("Lock Track", "トラックをロック"),
    "解锁轨道": ("Unlock Track", "トラックのロックを解除"),
    "轨道上移": ("Move Track Up", "トラックを上へ移動"),
    "轨道下移": ("Move Track Down", "トラックを下へ移動"),
    "轨道路由": ("Track Routing", "トラックルーティング"),
    "效果链": ("Effect Chain", "エフェクトチェーン"),
    "参数均衡器": ("Parametric EQ", "パラメトリックEQ"),
    "高通": ("High-pass", "ハイパス"),
    "噪声门": ("Noise Gate", "ノイズゲート"),
    "声道映射": ("Channel Mapping", "チャンネルマッピング"),
    "压缩器": ("Compressor", "コンプレッサー"),
    "限制器": ("Limiter", "リミッター"),
    "RNNoise": ("RNNoise", "RNNoise"),
    "响度标准化": ("Loudness Normalization", "ラウドネス正規化"),
    "自动闪避": ("Auto Ducking", "オートダッキング"),
    "选择一条音频总线": ("Select an audio bus", "オーディオバスを選択"),
    "选择总线后可添加、旁通并配置内置效果。": (
        "Select a bus to add, bypass, and configure built-in effects.",
        "バスを選択すると、内蔵エフェクトの追加、バイパス、設定ができます。",
    ),
    "选择离线素材所在目录": (
        "Choose the Folder Containing Offline Media",
        "オフラインメディアがあるフォルダーを選択",
    ),
    "批量重新定位 (%1)": ("Batch Relink (%1)", "一括再リンク (%1)"),
    "转场与效果": ("Transitions & Effects", "トランジションとエフェクト"),
    "选择一个片段，在它与同轨道下一个相邻片段之间创建转场。": (
        "Select a clip to create a transition to the next adjacent clip on the same track.",
        "クリップを選択し、同じトラックの次の隣接クリップとの間にトランジションを作成します。",
    ),
    "交叉溶解": ("Cross Dissolve", "クロスディゾルブ"),
    "淡化": ("Fade", "フェード"),
    "淡黑": ("Fade to Black", "黒へフェード"),
    "左擦除": ("Wipe Left", "左ワイプ"),
    "右擦除": ("Wipe Right", "右ワイプ"),
    "左滑动": ("Slide Left", "左スライド"),
    "右滑动": ("Slide Right", "右スライド"),
    "缩放": ("Zoom", "ズーム"),
    "片段变换": ("Clip Transform", "クリップ変形"),
    "位置、缩放、旋转、裁剪、透明度、速度和淡入淡出由右侧检查器编辑。所有修改进入同一个撤销栈。": (
        "Edit position, scale, rotation, crop, opacity, speed, and fades in the inspector. All changes use one undo stack.",
        "位置、スケール、回転、クロップ、不透明度、速度、フェードは右側のインスペクターで編集します。すべての変更は同じアンドゥ履歴に入ります。",
    ),
    "选择时间线片段": ("Select a timeline clip", "タイムラインクリップを選択"),
    "选择后可以添加转场并编辑片段属性。": (
        "Select a clip to add transitions and edit its properties.",
        "選択するとトランジションの追加とクリップ属性の編集ができます。",
    ),
    "MP4 视频 (*.mp4)": ("MP4 Video (*.mp4)", "MP4 動画 (*.mp4)"),
    "MKV 视频 (*.mkv)": ("MKV Video (*.mkv)", "MKV 動画 (*.mkv)"),
    "MOV 视频 (*.mov)": ("MOV Video (*.mov)", "MOV 動画 (*.mov)"),
    "FLAC 音频 (*.flac)": ("FLAC Audio (*.flac)", "FLAC 音声 (*.flac)"),
    "导出序列": ("Export Sequence", "シーケンスを書き出す"),
    "导出": ("Export", "書き出し"),
    "当前序列": ("Current Sequence", "現在のシーケンス"),
    "HDR10 · BT.2020 · PQ": ("HDR10 · BT.2020 · PQ", "HDR10 · BT.2020 · PQ"),
    "SDR · BT.709": ("SDR · BT.709", "SDR · BT.709"),
    "格式": ("Format", "形式"),
    "编码器": ("Encoder", "エンコーダー"),
    "像素格式": ("Pixel Format", "ピクセル形式"),
    "编码预设": ("Encoding Preset", "エンコードプリセット"),
    "宽": ("Width", "幅"),
    "高": ("Height", "高さ"),
    "目标码率 kbps（0=质量模式）": (
        "Target bitrate kbps (0 = quality mode)",
        "目標ビットレート kbps（0 = 品質モード）",
    ),
    "最大码率 kbps（0=不限）": (
        "Maximum bitrate kbps (0 = unlimited)",
        "最大ビットレート kbps（0 = 無制限）",
    ),
    "音频编码器": ("Audio Encoder", "オーディオエンコーダー"),
    "音频 kbps": ("Audio kbps", "音声 kbps"),
    "母版显示元数据": ("Mastering Display Metadata", "マスタリングディスプレイメタデータ"),
    "导出后会实际运行 ffprobe，校验分辨率、编码、位深、色彩和 HDR 母版元数据。": (
        "After export, ffprobe verifies resolution, codec, bit depth, color, and HDR mastering metadata.",
        "書き出し後に ffprobe を実行し、解像度、コーデック、ビット深度、色、HDR マスタリングメタデータを検証します。",
    ),
    "选择位置并导出": ("Choose Location and Export", "保存先を選んで書き出す"),
    "导出使用原始素材和与预览相同的 MLT 时间线图。进度、失败原因及产物入口显示在任务抽屉。": (
        "Export uses original media and the same MLT timeline graph as preview. Progress, errors, and outputs appear in the task drawer.",
        "書き出しには元のメディアとプレビューと同じ MLT タイムライングラフを使用します。進捗、失敗理由、出力はタスクドロワーに表示されます。",
    ),
    "AI 高光": ("AI Highlights", "AI ハイライト"),
    "分析": ("Analyze", "分析"),
    "候选区间保存在项目中，可直接生成独立的 9:16 短视频序列。": (
        "Candidate ranges are saved in the project and can create independent 9:16 short-video sequences.",
        "候補区間はプロジェクトに保存され、独立した 9:16 ショート動画シーケンスを作成できます。",
    ),
    "创建短视频序列": ("Create Short-Video Sequence", "ショート動画シーケンスを作成"),
    "还没有高光候选": ("No highlight candidates yet", "ハイライト候補はまだありません"),
    "选择字幕文档并运行分析。候选结果会显示在这里。": (
        "Select a subtitle document and run analysis. Candidates will appear here.",
        "字幕ドキュメントを選択して分析を実行すると、候補がここに表示されます。",
    ),
    "选择项目保存位置": ("Choose Project Location", "プロジェクトの保存先を選択"),
    "选择包含 project.mfp 的项目目录": (
        "Choose a project folder containing project.mfp",
        "project.mfp を含むプロジェクトフォルダーを選択",
    ),
    "选择要导入的媒体": ("Choose Media to Import", "読み込むメディアを選択"),
    "媒体文件 (*.mp4 *.mov *.mkv *.webm *.avi *.mp3 *.wav *.flac *.aac *.m4a *.png *.jpg *.jpeg *.webp *.srt)": (
        "Media Files (*.mp4 *.mov *.mkv *.webm *.avi *.mp3 *.wav *.flac *.aac *.m4a *.png *.jpg *.jpeg *.webp *.srt)",
        "メディアファイル (*.mp4 *.mov *.mkv *.webm *.avi *.mp3 *.wav *.flac *.aac *.m4a *.png *.jpg *.jpeg *.webp *.srt)",
    ),
    "所有文件 (*)": ("All Files (*)", "すべてのファイル (*)"),
    "选择下载所属项目": ("Choose a Project for the Download", "ダウンロード先プロジェクトを選択"),
    "选择素材所属项目": ("Choose a Project for the Media", "メディアの所属プロジェクトを選択"),
    "所有素材和任务必须属于一个项目。可以使用左侧填写的名称新建项目，也可以打开已有项目。": (
        "All media and tasks must belong to a project. Create one using the name on the left, or open an existing project.",
        "すべてのメディアとタスクはプロジェクトに属する必要があります。左側の名前で新規作成するか、既存のプロジェクトを開いてください。",
    ),
    "新建项目": ("New Project", "新規プロジェクト"),
    "打开项目": ("Open Project", "プロジェクトを開く"),
    "MediaFlow Pro": ("MediaFlow Pro", "MediaFlow Pro"),
    "从下载、转录和翻译，到多轨编辑与专业导出。所有工作都保存在一个可移动的项目目录中。": (
        "From download, transcription, and translation to multitrack editing and professional export. Everything stays in one portable project folder.",
        "ダウンロード、文字起こし、翻訳からマルチトラック編集、プロ向け書き出しまで。すべてを移動可能なプロジェクトフォルダーに保存します。",
    ),
    "例如：产品发布视频": ("For example: Product launch video", "例：製品発表動画"),
    "选择位置并创建": ("Choose Location and Create", "保存先を選んで作成"),
    "打开已有项目": ("Open Existing Project", "既存のプロジェクトを開く"),
    "快速开始": ("Quick Start", "クイックスタート"),
    "粘贴视频或播放列表链接": ("Paste a video or playlist URL", "動画またはプレイリストのURLを貼り付け"),
    "选择项目并下载": ("Choose Project and Download", "プロジェクトを選んでダウンロード"),
    "导入本地素材": ("Import Local Media", "ローカルメディアを読み込む"),
    "最近项目": ("Recent Projects", "最近のプロジェクト"),
    "项目目录可直接复制或移动": (
        "Project folders can be copied or moved directly",
        "プロジェクトフォルダーは直接コピーまたは移動できます",
    ),
    "项目已移动或不可用": ("Project moved or unavailable", "プロジェクトが移動されたか利用できません"),
    "打开 ›": ("Open ›", "開く ›"),
    "离线": ("Offline", "オフライン"),
    "还没有最近项目": ("No recent projects yet", "最近のプロジェクトはありません"),
    "创建第一个项目后，下载、字幕、短视频和导出结果都会集中保存在项目目录中。": (
        "After creating your first project, downloads, subtitles, short videos, and exports are kept together in its folder.",
        "最初のプロジェクトを作成すると、ダウンロード、字幕、ショート動画、書き出し結果がプロジェクトフォルダーにまとめて保存されます。",
    ),
    "纯本地项目": ("Local-Only Projects", "完全ローカルのプロジェクト"),
    "不启动本地 Web 服务，也不打开浏览器窗口。下载由 yt-dlp 直接完成。": (
        "No local web service or browser window is started. yt-dlp handles downloads directly.",
        "ローカルWebサービスやブラウザーウィンドウは起動しません。ダウンロードは yt-dlp が直接行います。",
    ),
    "重新定位离线素材": ("Relink Offline Media", "オフラインメディアを再リンク"),
    "替换为不同内容？": ("Replace with different content?", "異なる内容に置き換えますか？"),
    "所选文件的内容指纹与原素材不同： %1 只有确认替换后才会关联，相关代理与波形会失效。": (
        "The selected file fingerprint differs from the original:\n%1\n\nIt will only be linked after confirmation; related proxies and waveforms will be invalidated.",
        "選択したファイルのフィンガープリントが元のメディアと異なります：\n%1\n\n確認後にのみ再リンクされ、関連するプロキシと波形は無効になります。",
    ),
    "检查器": ("Inspector", "インスペクター"),
    "素材": ("Media", "メディア"),
    "重新定位素材": ("Relink Media", "メディアを再リンク"),
    "生成代理": ("Generate Proxy", "プロキシを生成"),
    "生成波形": ("Generate Waveform", "波形を生成"),
    "添加到时间线": ("Add to Timeline", "タイムラインに追加"),
    "时间线片段": ("Timeline Clip", "タイムラインクリップ"),
    "裁剪与速度": ("Trim & Speed", "トリムと速度"),
    "源入点": ("Source In", "ソースイン"),
    "持续帧": ("Duration (frames)", "長さ（フレーム）"),
    "保音高": ("Preserve Pitch", "ピッチを維持"),
    "应用裁剪": ("Apply Trim", "トリムを適用"),
    "应用速度": ("Apply Speed", "速度を適用"),
    "画面变换": ("Visual Transform", "映像変形"),
    "横向缩放": ("Horizontal Scale", "横方向スケール"),
    "纵向缩放": ("Vertical Scale", "縦方向スケール"),
    "旋转 °": ("Rotation °", "回転 °"),
    "透明度": ("Opacity", "不透明度"),
    "裁左": ("Crop Left", "左クロップ"),
    "裁上": ("Crop Top", "上クロップ"),
    "裁右": ("Crop Right", "右クロップ"),
    "裁下": ("Crop Bottom", "下クロップ"),
    "应用画面参数": ("Apply Visual Settings", "映像設定を適用"),
    "片段音频": ("Clip Audio", "クリップ音声"),
    "增益 dB": ("Gain dB", "ゲイン dB"),
    "声像": ("Pan", "パン"),
    "淡入帧": ("Fade-in Frames", "フェードインフレーム"),
    "淡出帧": ("Fade-out Frames", "フェードアウトフレーム"),
    "应用音频参数": ("Apply Audio Settings", "音声設定を適用"),
    "删除": ("Delete", "削除"),
    "波纹删除": ("Ripple Delete", "リップル削除"),
    "这里空空如也": ("Nothing selected", "何も選択されていません"),
    "选择素材、片段、轨道、字幕或音频总线后，可在这里编辑属性。": (
        "Select media, a clip, track, subtitle, or audio bus to edit its properties here.",
        "メディア、クリップ、トラック、字幕、オーディオバスを選択すると、ここで属性を編集できます。",
    ),
    "导入媒体": ("Import Media", "メディアを読み込む"),
    "媒体文件 (*.mp4 *.mov *.mkv *.webm *.mp3 *.wav *.flac *.png *.jpg *.jpeg)": (
        "Media Files (*.mp4 *.mov *.mkv *.webm *.mp3 *.wav *.flac *.png *.jpg *.jpeg)",
        "メディアファイル (*.mp4 *.mov *.mkv *.webm *.mp3 *.wav *.flac *.png *.jpg *.jpeg)",
    ),
    "媒体": ("Media", "メディア"),
    "导入": ("Import", "読み込み"),
    "搜索素材": ("Search Media", "メディアを検索"),
    "可用": ("Available", "利用可能"),
    "代理": ("Proxy", "プロキシ"),
    "波形": ("Waveform", "波形"),
    "导入第一个素材": ("Import Your First Media", "最初のメディアを読み込む"),
    "支持视频、音频和图片。下载的视频也会自动出现在这里。": (
        "Supports video, audio, and images. Downloaded videos also appear here automatically.",
        "動画、音声、画像に対応しています。ダウンロードした動画も自動的にここへ表示されます。",
    ),
    "设置": ("Settings", "設定"),
    "常规": ("General", "一般"),
    "下载与媒体": ("Downloads & Media", "ダウンロードとメディア"),
    "AI": ("AI", "AI"),
    "界面": ("Interface", "インターフェース"),
    "语言": ("Language", "言語"),
    "主题": ("Theme", "テーマ"),
    "深色": ("Dark", "ダーク"),
    "高对比度": ("High Contrast", "ハイコントラスト"),
    "工作流自动继续（遇到缺少 API、语言不明确或离线素材时仍会停止）": (
        "Continue workflows automatically (still stops for missing APIs, unclear language, or offline media)",
        "ワークフローを自動継続（API不足、言語不明、オフラインメディアの場合は停止）",
    ),
    "预览与代理": ("Preview & Proxy", "プレビューとプロキシ"),
    "自动为高分辨率、高码率、VFR、10-bit/HDR 或持续掉帧素材生成代理": (
        "Automatically generate proxies for high resolution, high bitrate, VFR, 10-bit/HDR, or repeatedly dropped frames",
        "高解像度、高ビットレート、VFR、10-bit/HDR、または継続的にフレーム落ちするメディアのプロキシを自動生成",
    ),
    "预览质量": ("Preview Quality", "プレビュー品質"),
    "自动": ("Auto", "自動"),
    "原始素材": ("Original Media", "元のメディア"),
    "在设备支持时启用 HDR 预览": ("Enable HDR preview when supported", "対応デバイスでHDRプレビューを有効化"),
    "音频默认值": ("Audio Defaults", "音声の既定値"),
    "响度目标 ×10": ("Loudness Target ×10", "ラウドネス目標 ×10"),
    "True Peak ×10": ("True Peak ×10", "True Peak ×10"),
    "声道布局": ("Channel Layout", "チャンネルレイアウト"),
    "yt-dlp 下载": ("yt-dlp Downloads", "yt-dlp ダウンロード"),
    "格式，例如 best 或 1080p": ("Format, such as best or 1080p", "形式（例：best、1080p）"),
    "cookies.txt 路径（可选）": ("cookies.txt path (optional)", "cookies.txt のパス（任意）"),
    "读取浏览器 Cookie": ("Read Browser Cookies", "ブラウザーCookieを読み取る"),
    "不读取": ("Do Not Read", "読み取らない"),
    "不会启动浏览器窗口。浏览器 Cookie 由 yt-dlp 直接读取。": (
        "No browser window will open. yt-dlp reads browser cookies directly.",
        "ブラウザーウィンドウは開きません。ブラウザーCookieは yt-dlp が直接読み取ります。",
    ),
    "转录": ("Transcription", "文字起こし"),
    "faster-whisper 模型": ("faster-whisper Model", "faster-whisper モデル"),
    "设备": ("Device", "デバイス"),
    "计算类型，例如 float16 / int8": (
        "Compute type, such as float16 / int8",
        "計算タイプ（例：float16 / int8）",
    ),
    "语言代码，auto 为自动识别": (
        "Language code; auto detects automatically",
        "言語コード（auto は自動検出）",
    ),
    "OpenAI 兼容 LLM 提供商": ("OpenAI-Compatible LLM Provider", "OpenAI互換LLMプロバイダー"),
    "名称": ("Name", "名前"),
    "模型名称": ("Model Name", "モデル名"),
    "翻译和高光分析使用同一强类型配置。留空全部字段可禁用 LLM。": (
        "Translation and highlight analysis share one typed configuration. Leave all fields empty to disable the LLM.",
        "翻訳とハイライト分析は同じ型付き設定を使用します。すべて空欄にするとLLMを無効にできます。",
    ),
    "取消": ("Cancel", "キャンセル"),
    "保存设置": ("Save Settings", "設定を保存"),
    "任务中心": ("Task Center", "タスクセンター"),
    "暂停": ("Pause", "一時停止"),
    "继续": ("Resume", "再開"),
    "打开产物": ("Open Output", "出力を開く"),
    "没有后台任务": ("No background tasks", "バックグラウンドタスクはありません"),
    "下载、代理、转录、翻译和导出进度会显示在这里。": (
        "Download, proxy, transcription, translation, and export progress appears here.",
        "ダウンロード、プロキシ、文字起こし、翻訳、書き出しの進捗がここに表示されます。",
    ),
    "添加视频轨": ("Add Video Track", "ビデオトラックを追加"),
    "添加音频轨": ("Add Audio Track", "オーディオトラックを追加"),
    "添加字幕轨": ("Add Subtitle Track", "字幕トラックを追加"),
    "开始转录": ("Start Transcription", "文字起こしを開始"),
    "当前素材已选中。模型、语言和设备使用项目的 ASR 设置。": (
        "The current media is selected. Model, language, and device use the project's ASR settings.",
        "現在のメディアが選択されています。モデル、言語、デバイスにはプロジェクトのASR設定を使用します。",
    ),
    "请先到“媒体”模式选择一个视频或音频素材。": (
        "First select video or audio media in Media mode.",
        "先に「メディア」モードで動画または音声を選択してください。",
    ),
    "字幕文档": ("Subtitle Documents", "字幕ドキュメント"),
    "源字幕": ("Source Subtitles", "元の字幕"),
    "翻译": ("Translation", "翻訳"),
    "条": (" items", " 件"),
    "转录文本": ("Transcript", "文字起こしテキスト"),
    "放入序列": ("Place in Sequence", "シーケンスに配置"),
    "还没有转录结果": ("No transcription yet", "文字起こし結果はまだありません"),
    "选择媒体素材并开始转录，结果会直接保存到项目。": (
        "Select media and start transcription. Results are saved directly to the project.",
        "メディアを選択して文字起こしを開始すると、結果がプロジェクトに直接保存されます。",
    ),
    "选择一个源字幕文档。译文会保留稳定的源分段关联，不会覆盖原文。": (
        "Select a source subtitle document. Translations keep stable source-segment links and never overwrite the original.",
        "元の字幕ドキュメントを選択してください。翻訳は元セグメントとの安定した関連を保持し、原文を上書きしません。",
    ),
    "源文档": ("Source Document", "元ドキュメント"),
    "译文": ("Translation", "訳文"),
    "目标语言": ("Target Language", "翻訳先言語"),
    "简体中文": ("Simplified Chinese", "簡体字中国語"),
    "英语": ("English", "英語"),
    "日语": ("Japanese", "日本語"),
    "繁体中文": ("Traditional Chinese", "繁体字中国語"),
    "韩语": ("Korean", "韓国語"),
    "西班牙语": ("Spanish", "スペイン語"),
    "翻译所选文档": ("Translate Selected Document", "選択したドキュメントを翻訳"),
    "执行规则": ("Execution Rules", "実行ルール"),
    "缺少 API 配置时任务会明确失败并保留原因，不会猜测翻译结果。": (
        "If API configuration is missing, the task fails explicitly and keeps the reason; it never invents a translation.",
        "API設定がない場合、タスクは理由を残して明確に失敗し、翻訳結果を推測しません。",
    ),
    "只读": ("Read Only", "読み取り専用"),
    "任务": ("Tasks", "タスク"),
    "关闭": ("Close", "閉じる"),
    "短视频": ("Short Video", "ショート動画"),
    "设": ("Set", "設定"),
    "预览不可用：": ("Preview unavailable: ", "プレビューを利用できません："),
    "把素材添加到时间线开始创作": (
        "Add media to the timeline to start creating",
        "メディアをタイムラインに追加して編集を開始",
    ),
    "HDR 项目 / SDR 预览": ("HDR Project / SDR Preview", "HDRプロジェクト / SDRプレビュー"),
    "HDR 预览": ("HDR Preview", "HDRプレビュー"),
    "掉帧": ("Dropped ", "フレーム落ち "),
    "新建短视频序列": ("New Short-Video Sequence", "新規ショート動画シーケンス"),
    "序列配置": ("Sequence Settings", "シーケンス設定"),
    "画布比例": ("Canvas Aspect", "キャンバス比率"),
    "宽度": ("Width", "幅"),
    "高度": ("Height", "高さ"),
    "帧率": ("Frame Rate", "フレームレート"),
    "色彩与输出声道": ("Color and Output Channels", "カラーと出力チャンネル"),
    "单声道": ("Mono", "モノラル"),
    "立体声": ("Stereo", "ステレオ"),
    "修改帧率会按实际时长重新换算片段、转场和字幕；主序列的代理会自动失效并按需重建。": (
        "Changing the frame rate retimes clips, transitions, and subtitles by real duration. Main-sequence proxies are invalidated and rebuilt as needed.",
        "フレームレートを変更すると、クリップ、トランジション、字幕が実時間に基づいて再計算されます。メインシーケンスのプロキシは無効化され、必要に応じて再生成されます。",
    ),
    "采用视频项目配置？": ("Adopt the video's project profile?", "動画のプロジェクト設定を採用しますか？"),
    "主时间线中已经有图片或音频编辑。这个视频建议使用 %1。采用后会按实际时长重新换算现有编辑；选择“否”则保持当前项目配置。": (
        "The main timeline already contains image or audio edits. This video recommends %1. Adopting it retimes existing edits by actual duration; choosing No keeps the current project profile.",
        "メインタイムラインには画像または音声の編集があります。この動画には %1 を推奨します。採用すると既存の編集を実時間に基づいて再計算し、「いいえ」を選ぶと現在のプロジェクト設定を維持します。",
    ),
    "工作流：跟随全局": ("Workflow: Follow Global", "ワークフロー：グローバル設定"),
    "工作流：每步确认": ("Workflow: Confirm Each Stage", "ワークフロー：各段階で確認"),
    "工作流：自动继续": ("Workflow: Auto Continue", "ワークフロー：自動続行"),
    "下载": ("Download", "ダウンロード"),
    "媒体分析、代理与波形": ("Media Analysis, Proxy & Waveform", "メディア解析・プロキシ・波形"),
    "AI 高光分析": ("AI Highlight Analysis", "AI ハイライト分析"),
    "创建短视频草稿": ("Create Short-Video Drafts", "ショート動画の下書きを作成"),
    "工作流": ("Workflow", "ワークフロー"),
    "请选择目标语言后继续。": (
        "Choose a target language to continue.",
        "続行するには対象言語を選択してください。",
    ),
    "需要先配置并启用 LLM 提供商。": (
        "Configure and enable an LLM provider first.",
        "先に LLM プロバイダーを設定して有効にしてください。",
    ),
    "请在导出页选择格式和保存位置。": (
        "Choose a format and save location on the Export page.",
        "書き出しページで形式と保存先を選択してください。",
    ),
    "工作流包含离线素材，请先重新关联。": (
        "This workflow contains offline media. Relink it first.",
        "このワークフローにはオフラインメディアがあります。先に再リンクしてください。",
    ),
    "阶段任务失败，可在任务中心查看原因后重试。": (
        "The stage failed. Review the cause in Tasks, then retry.",
        "ステージに失敗しました。タスクで原因を確認し、再試行してください。",
    ),
    "阶段任务已取消，可重新继续。": (
        "The stage was cancelled. You can continue it again.",
        "ステージはキャンセルされました。再度続行できます。",
    ),
    "正在执行，进度可在任务中心查看。": (
        "Running. Progress is available in Tasks.",
        "実行中です。進捗はタスクで確認できます。",
    ),
    "上一阶段已完成，确认后继续。": (
        "The previous stage is complete. Confirm to continue.",
        "前のステージが完了しました。確認して続行してください。",
    ),
    "工作流已暂停，请处理当前阶段。": (
        "The workflow is paused. Resolve the current stage.",
        "ワークフローは一時停止中です。現在のステージを処理してください。",
    ),
    "选择目标语言": ("Choose Target Language", "対象言語を選択"),
    "中文": ("Chinese", "中国語"),
    "日本语": ("Japanese", "日本語"),
    "打开设置": ("Open Settings", "設定を開く"),
    "前往导出": ("Go to Export", "書き出しへ"),
    "取消工作流": ("Cancel Workflow", "ワークフローをキャンセル"),
}


def normalize(value: str) -> str:
    return " ".join(value.split())


def update_catalog(path: Path, language_index: int) -> None:
    tree = ET.parse(path)
    root = tree.getroot()
    missing: set[str] = set()
    for message in root.findall("./context/message"):
        source = message.find("source")
        translation = message.find("translation")
        if source is None or translation is None:
            continue
        key = normalize(source.text or "")
        pair = TRANSLATIONS.get(key)
        if pair is None:
            missing.add(key)
            continue
        value = pair[language_index]
        if sorted(re.findall(r"%\d+", value)) != sorted(re.findall(r"%\d+", source.text or "")):
            raise ValueError(f"Placeholder mismatch for {key!r}: {value!r}")
        translation.attrib.clear()
        translation.text = value
    if missing:
        raise KeyError("Missing translations:\n" + "\n".join(sorted(missing)))
    ET.indent(tree, space="    ")
    tree.write(path, encoding="utf-8", xml_declaration=True)


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    i18n = root / "mediaflow" / "resources" / "i18n"
    catalogs = [(i18n / "mediaflow_en.ts", 0), (i18n / "mediaflow_ja.ts", 1)]
    lupdate = Path(sys.executable).with_name("pyside6-lupdate.exe")
    lrelease = Path(sys.executable).with_name("pyside6-lrelease.exe")
    if not lupdate.is_file():
        raise FileNotFoundError(lupdate)
    if not lrelease.is_file():
        raise FileNotFoundError(lrelease)
    subprocess.run(
        [
            str(lupdate),
            str(root / "mediaflow" / "desktop" / "qml"),
            "-no-obsolete",
            "-ts",
            *(str(path) for path, _ in catalogs),
        ],
        check=True,
    )
    for path, language_index in catalogs:
        update_catalog(path, language_index)
        qm = path.with_suffix(".qm")
        subprocess.run(
            [str(lrelease), str(path), "-qm", str(qm)],
            check=True,
        )


if __name__ == "__main__":
    main()

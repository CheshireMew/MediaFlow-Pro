<p align="center">
  <img src="mediaflow/resources/branding/mediaflow-mark.svg" width="112" alt="MediaFlow Pro ロゴ">
</p>

# MediaFlow Pro

<!-- readme-header:start -->

<p align="center">
  <a href="./README.md">中文</a> · <a href="./README.en.md">English</a> · <strong>日本語</strong> | <a href="./ARCHITECTURE.md">ドキュメント</a> | <a href="./CONTRIBUTING.md">コントリビュート</a> | <a href="https://github.com/CheshireMew/MediaFlow-Pro/issues">問題報告</a>
</p>

<p align="center">
  <a href="https://x.com/0xCheshire" title="X"><img src="https://img.shields.io/badge/X-%400xCheshire-000000?logo=x&amp;logoColor=white" alt="X：@0xCheshire"></a>
  <a href="https://t.me/CheshireBTC" title="Telegram"><img src="https://img.shields.io/badge/Telegram-CheshireBTC-26A5E4?logo=telegram&amp;logoColor=white" alt="Telegram：CheshireBTC"></a>
  <a href="https://blog.blacknico.com/" title="ブログ"><img src="https://img.shields.io/badge/Blog-blog.blacknico.com-2E7D32?logo=rss&amp;logoColor=white" alt="ブログ：blog.blacknico.com"></a>
  <a href="https://blacknico.com/" title="ホームページ"><img src="https://img.shields.io/badge/Home-blacknico.com-1F6FEB?logo=googlechrome&amp;logoColor=white" alt="ホームページ：blacknico.com"></a>
</p>

<p align="center">
  <a href="https://github.com/CheshireMew/MediaFlow-Pro/stargazers"><img src="https://img.shields.io/github/stars/CheshireMew/MediaFlow-Pro?style=flat" alt="GitHub Stars"></a>
  <a href="https://github.com/CheshireMew/MediaFlow-Pro/forks"><img src="https://img.shields.io/github/forks/CheshireMew/MediaFlow-Pro?style=flat" alt="GitHub Forks"></a>
  <a href="https://github.com/CheshireMew/MediaFlow-Pro/blob/main/LICENSE"><img src="https://img.shields.io/github/license/CheshireMew/MediaFlow-Pro?style=flat" alt="Repository License"></a>
</p>

<!-- readme-header:end -->

MediaFlow Pro は、プロジェクト単位で作業するローカル動画制作ワークステーションです。動画、音声、画像、字幕、メディアリンク、または `editable-media` v6 ウェブパッケージを渡すと、素材管理、文字起こし、編集、マルチトラックタイムライン、リアルタイムプレビュー、ミキシング、品質確認、最終書き出しまでを一つの移動可能なプロジェクト内で完結できます。

`project.mfp` がプロジェクト状態の唯一の正本です。プレビューと書き出しは同じタイムラインからコンパイルされるため、編集画面と最終成果物が別々の実装を通ることはありません。

[クイックスタート](#クイックスタート) · [機能](#できること) · [editable-media](#editable-media-ウェブパッケージ) · [CLI と MCP](#cli-と-mcp-による自動化) · [アーキテクチャ](ARCHITECTURE.md)

![MediaFlow Pro の中国語デスクトップワークスペース](docs/images/mediaflow-workspace-zh-cn.png)

<p align="center"><sub>実際の Qt/QML 受け入れ確認画面。素材、プレビュー、インスペクター、マルチトラックタイムラインを一つのデスクトップワークスペースにまとめています。</sub></p>

> [!IMPORTANT]
> このリポジトリが提供するのは、ソースコード、固定された依存関係、再現可能なビルド入口です。ビルド済みインストーラーは提供していません。Windows 10/11 x64 ではデスクトップ、ネイティブプレビュー、書き出しの一連のローカル検証を実施しています。Ubuntu 24.04 x64 と macOS 14+ Apple Silicon はソースビルド、ランタイム契約、CI ビルド、ネイティブプレビューのスモークテストに対応していますが、実機での完全なリリース検証済みとは表明していません。

## どのような作業に向いているか

| やりたいこと | MediaFlow Pro が提供する結果 |
| --- | --- |
| 素材映像、画像、音声、字幕から、繰り返し修正できる動画を作る | 移動可能なプロジェクト、マルチトラックタイムライン、同一経路のプレビューと書き出し |
| 構造化されたウェブアニメーションと通常の素材を同じタイムラインに置く | `editable-media` v6 の読み込み、共通フィールド編集、キーフレーム、パッケージ差し替え、実時刻フィルムストリップ、復旧可能な決定論的ブラウザレンダリング |
| 文字起こしを使って編集、翻訳、ハイライト抽出を行う | 文字起こしワークスペースと、事前確認・取り消しが可能な CLI 自動化 |
| 納品動画が要件を満たすか確認する | 黒フレーム、フリーズ、無音、ラウドネス、尺、セーフエリア、参照動画比較のレポート |
| オンラインメディアをダウンロードして編集を続ける | yt-dlpによる動画・プレイリスト、小宇宙エピソード音声のダウンロード、プロジェクト作成、実進捗の表示 |

完成済み HTML アニメーションを決定論的に動画化するだけで、ノンリニア編集プロジェクトが不要なら、[HyperFrames](https://github.com/heygen-com/hyperframes) の方が直接的です。MediaFlow Pro は、実写素材、複数トラック、字幕、音声、繰り返し修正を一つの制作工程で扱う場合に向いています。

## クイックスタート

### 1. 環境を準備する

Python 3.12、対象プラットフォームの C++20 ツールチェーン、および Qt、MLT、FFmpeg、Chromium、キャッシュ、メディアを保存できる十分な空き容量が必要です。[`runtime.lock.json`](runtime.lock.json) がランタイムのバージョンと SHA-256 を固定し、[`requirements.lock`](requirements.lock) が Python 依存関係を固定します。

まず [`.env.example`](.env.example) をコピーし、次の三つのマシン固有ルートを設定します。

| 変数 | 用途 |
| --- | --- |
| `MEDIAFLOW_DEV_ROOT` | Python 環境、SDK、ランタイム、ビルド、キャッシュ |
| `MEDIAFLOW_PROJECT_ROOT` | 新規プロジェクトの既定保存先 |
| `MEDIAFLOW_MEDIA_ROOT` | ダウンロード、読み込み、文字起こし対象メディアのアプリケーション共通ルート |

Windows PowerShell：

```powershell
Copy-Item .env.example .env
# 続行する前に .env を編集
. .\scripts\load_environment.ps1

py -3.12 -m venv (Join-Path $env:MEDIAFLOW_DEV_ROOT ".venv")
& $env:MEDIAFLOW_PYTHON -m pip install --require-hashes -r requirements.lock
& $env:MEDIAFLOW_PYTHON -m pip install --no-deps --no-build-isolation -e .

& $env:MEDIAFLOW_PYTHON scripts\prepare_runtime.py --runtime-root $env:MEDIAFLOW_RUNTIME_DIR
& $env:MEDIAFLOW_PYTHON scripts\prepare_ci_qt.py --qt-root (Join-Path $env:MEDIAFLOW_RUNTIME_DIR "qt")
& $env:MEDIAFLOW_PYTHON scripts\build_native.py --runtime-root $env:MEDIAFLOW_RUNTIME_DIR
```

<details>
<summary>Ubuntu / macOS のコマンド</summary>

```bash
cp .env.example .env
# 続行する前に .env を編集
set -a
. ./.env
set +a

export MEDIAFLOW_RUNTIME_DIR="${MEDIAFLOW_RUNTIME_DIR:-$MEDIAFLOW_DEV_ROOT/runtime}"
export MEDIAFLOW_PYTHON="${MEDIAFLOW_PYTHON:-$MEDIAFLOW_DEV_ROOT/.venv/bin/python}"

python3.12 -m venv "$MEDIAFLOW_DEV_ROOT/.venv"
"$MEDIAFLOW_PYTHON" -m pip install --require-hashes -r requirements.lock
"$MEDIAFLOW_PYTHON" -m pip install --no-deps --no-build-isolation -e .

"$MEDIAFLOW_PYTHON" scripts/prepare_runtime.py --runtime-root "$MEDIAFLOW_RUNTIME_DIR"
"$MEDIAFLOW_PYTHON" scripts/prepare_ci_qt.py --qt-root "$MEDIAFLOW_RUNTIME_DIR/qt"
"$MEDIAFLOW_PYTHON" scripts/build_native.py --runtime-root "$MEDIAFLOW_RUNTIME_DIR"
```

</details>

### 2. アプリケーションを起動する

Windows：

```powershell
.\scripts\launch.ps1
```

Ubuntu / macOS：

```bash
"$MEDIAFLOW_PYTHON" -m mediaflow.desktop.app
```

三つのプラットフォームすべてで、プロジェクトディレクトリを最初の引数として渡すと直接開けます。

### 3. 最初の編集を完了する

1. ホーム画面で空のプロジェクトを作成するか、内蔵サンプルを開いて画面全体を確認します。
2. ローカルメディア、字幕、ウェブパッケージを読み込むか、メディアリンクを貼り付けてダウンロードを開始します。
3. 素材をタイムラインへドラッグし、クリップ、字幕、音声、画面設定を編集します。
4. プログラムモニターで最終合成を確認し、「書き出し」から動画と品質レポートを生成します。

![MediaFlow Pro の中国語ホーム画面](docs/images/mediaflow-home-zh-cn.png)

<p align="center"><sub>ホーム画面では、プロジェクト作成、リンクからのダウンロード、既存プロジェクトやサンプルの表示を開始できます。</sub></p>

## できること

| ワークスペース | 実装済み機能 |
| --- | --- |
| プロジェクトと素材 | 移動可能なプロジェクトディレクトリ、素材フォルダー、プロキシ、波形、フィンガープリント、オフライン検出、再リンク、バージョンスナップショット |
| タイムライン | 複数シーケンス、映像・音声・字幕トラック、トリム、分割、複製、リップル削除、トランジション、ソース差し替え、速度変更、逆再生、複合クリップ、エフェクトチェーン、統一された元に戻す・やり直す |
| プレビューと映像 | ソースモニター、プログラムモニター、キャンバス変形、ネイティブ音声クロック、HDR/SDR プロジェクト、MLT ベースのプレビュー |
| テキストと字幕 | faster-whisper / Faster-Whisper XXL 文字起こし、字幕編集、翻訳、用語集、複数話者のダイアライゼーション、実際の単語時刻に基づくテキスト編集 |
| 音声 | 複数バス、エフェクトチェーン、ダッキング、LUFS、True Peak 測定、話者別のクロス言語音声クローン |
| 分析と納品 | シーンカット、被写体追跡、黒フレーム・フリーズ・無音検査、参照動画とのフレーム比較、H.264/HEVC/AV1/ProRes、字幕単体、FCPXML |
| UI とワークスペース | 中国語・英語・日本語 UI、高 DPI、キーボード操作、保存可能な標準・メディア・縦型レイアウト |

ダウンロード機能と任意ランタイムの利用可否は、対象サイトとローカル環境に依存します。ログイン済みのリモートページは `editable-media` 読み込みの対象外です。ウェブパッケージはローカルにあり、検証可能で、決定論的にシークできる必要があります。

## 複数話者のクロス言語吹き替え

「テキストと字幕」ワークスペースでは、発話が重ならない英語の会話を中国語音声に変換できます。音声を主要会話トラックに配置した後、英語字幕がまだなければ、ワークスペースを切り替えずに吹き替えパネルから正式な文字起こしタスクを開始できます。指定した会話トラックを pyannote.audio Community-1 で話者分離し、実際の単語時刻から音声と原文が一致する 3.0–9.8 秒の参照音声を話者ごとに複数抽出します。単語時刻のない長い字幕を切り出す場合は、参照原文の確認が明示的に必要になります。字幕の一対一翻訳を保ったまま、一つの GPT-SoVITS v2Pro サービスで一括合成し、話者、参照音声、翻訳、確認状態を編集してから、差し替え可能な一本の音声トラックとしてタイムラインへ反映できます。長い発話は後続の無音を使い、上限付きで速度を調整し、それでも長い場合は末尾を切らず、全体を残して要確認にします。

pyannote は独立した Python 環境に置き、[Community-1 の利用条件](https://huggingface.co/pyannote/speaker-diarization-community-1)に同意したうえで、設定画面からその Python、Hugging Face トークン、GPT-SoVITS v2Pro を選択してください。Hugging Face、モデル、Torch のキャッシュはその環境の `cache` ディレクトリに保存され、認証付きで一度正常に話者分離できれば、その後はトークンを外してオフライン実行できます。公開操作は `dubbing.prepare`、三つのレビュー更新操作、`dubbing.synthesize`、`dubbing.commit` です。正確な契約は `mediaflow-cli describe --operation <name>` で確認できます。

## `editable-media` ウェブパッケージ

MediaFlow Pro は、汎用的なローカル `editable-media` v6 パッケージを正式に受け入れます。特定の生産者リポジトリの構成やサンプル名には依存しません。DOM、React、その他のフロントエンド技術はパッケージの生産手段にすぎず、読み込み後はすべて通常の Web 素材となり、二つ目のプロジェクト状態や書き出し経路は作りません。

- `window.editableMedia` は、テキスト、スタイル、バリアント、シーン、レイヤー、パラメーター、素材スロットの構造化状態を公開します。
- `window.__hf.duration`、非同期 `window.__hf.seek(seconds)`、登録 renderer、frame task が、決定論的なフレーム時刻と準備完了の唯一の境界です。
- パッケージは、メディアをブラウザで描画するか、ネイティブ映像の下層またはネイティブ音声として合成するかを明示します。MediaFlow Pro は拡張子から推測しません。
- 元のパッケージは書き換えません。クリップ状態、差し替え履歴、プロジェクト参照は `project.mfp` に保存し、プロジェクトへ公開済みのコピーは変更しません。
- ブラウザ画像、ネイティブ映像、ネイティブ音声は一つのキャッシュと FFmpeg エンコード経路に入り、プレビュー、タイムライン、書き出しが共同で利用します。

旧プロジェクトの標準 v4/v5 ウェブ素材は、一度のトランザクション更新で直接 v6 へ移行します。旧パッケージは手動確認用としてプロジェクト内の `archive/web` へ移り、別の実行経路には残りません。安全に変換できると証明できない第三者 runtime は更新を中止し、再公開を要求します。

## CLI と MCP による自動化

`mediaflow-cli` は常駐 Editor Service の構造化クライアントです。最初の呼び出しでサービスを必要に応じて起動し、以後のコマンドはリクエストだけを送ります。CLI が `project.mfp` を直接開いたり、プロジェクトの書き込みロックを迂回したりすることはありません。

最初に、現在のマシンが公開する操作、パラメーター、ランタイム要件を確認します。

```powershell
mediaflow-cli describe
```

次に、ファイルまたは標準入力から `mediaflow-editor` v4 JSON を送ります。

```powershell
mediaflow-cli execute --request request.json
Get-Content request.json -Raw | mediaflow-cli execute --request -
```

書き込みリクエストには、安定した `request_id`、直前の読み取りで取得した `base_revision`、`actor`、`client_id` を使います。同一の再試行は永続化された受領結果を再利用します。古いリビジョンでも変更経路が重ならなければ再適用でき、競合する書き込みは暗黙に上書きせず明示的に失敗します。

書き出し、文字起こし、Web フィールドとキーフレーム、パッケージ差し替え、プロジェクト引き継ぎ、診断の各画面から、同じ実行可能リクエストを確認してコピーできます。コピーだけではタスクを開始せず、プロジェクト revision も増えません。`diagnostics.bundle.create` は、元メディアと認証情報を除外した容量制限付き診断 ZIP を生成する永続タスクです。

MCP 対応ホストでは、`mediaflow-mcp` を stdio server として設定できます。デスクトップや CLI と同じ Editor Service を共有し、二つ目の編集実装は持ちません。操作とパラメーターの正本は、引き続き `mediaflow-cli describe` の実際の出力です。

## プロジェクトとアーキテクチャの境界

```text
<MEDIAFLOW_PROJECT_ROOT>/
  <ProjectName>/
    project.mfp
    generated/
    proxies/
    cache/
    exports/
```

- `project.mfp` は、プロジェクトモデル、タイムライン、字幕、ウェブクリップ状態、バージョン情報の唯一の正本です。
- MLT グラフ、ウェブレンダーキャッシュ、プロキシ、波形、分析レポートは再生成可能な派生成果物です。
- QML はデータベースへ直接アクセスせず、外部プロセスも直接起動しません。デスクトップ、CLI、MCP は同じ `EditorApplication` / `EditorProject` 境界を利用します。
- サインイン中の各ユーザーにはオンデマンドで起動するローカル Editor Service が一つだけあり、プロジェクトの書き込みロックを取得できるのはそのサービスプロセスだけです。
- `.env.example` がマシンパスの公開契約です。ソースコードはドライブ文字やシステムインストール先からランタイムを推測しません。
- 大容量キャッシュとローカル検証は、最初の書き込み前にプロジェクト上限と空き容量の安全ラインを確認します。`python scripts/report_storage.py` で実際のルート、プロジェクト所有者、削除候補を確認でき、このレポート自体はファイルを削除しません。

レイヤー構成、スレッドモデル、永続化境界、サービスプロトコルは [ARCHITECTURE.md](ARCHITECTURE.md) を参照してください。

## 開発と検証

ローカル環境を読み込み、変更を直接対象とするテストから実行してください。対象テストが通ったら、変更範囲に応じた最終検証を唯一のローカル品質入口から実行します。

```powershell
. .\scripts\load_environment.ps1
& $env:MEDIAFLOW_PYTHON -m pytest tests\v2\path\to\test_file.py
.\scripts\run_quality.ps1
```

資源境界のない `pytest tests/v2` 全体を直接実行しないでください。ローカル入口と CI はどちらも [`scripts/ci/quality_plan.py`](scripts/ci/quality_plan.py) を利用し、クロスプラットフォームのソースビルドとプロジェクト受け渡しを別々に判定します。文書だけの変更で、無関係なデスクトップテストやエンドツーエンドテストを起動することはありません。`.\scripts\run_quality.ps1 --dry-run` で実際のコマンドを確認できます。

デスクトップログはランタイムディレクトリ内の `logs/mediaflow.log` に保存され、5 MiB でローテーションし、五つのバックアップを保持します。エラーダイアログ末尾の短いコードもそのまま記録されます。[GitHub Issues](https://github.com/CheshireMew/MediaFlow-Pro/issues) へ報告するときは、そのコードを添えてください。

## ライセンスと配布

MediaFlow Pro のソースコードは [GNU GPL v3](LICENSE) で公開されています。Qt、MLT、FFmpeg、yt-dlp、Python パッケージ、その他のサードパーティーコンポーネントには、それぞれのライセンスが適用されます。詳細は [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) を参照してください。

このリポジトリが管理するのは、ソースコード、ビルドスクリプト、依存関係マニフェストです。プロジェクト所有者がリリース計画を明示的に開始しない限り、ポータブルパッケージやインストーラーは生成しません。

## Star History

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/CheshireMew/MediaFlow-Pro/star-history/star-history-dark.svg">
  <source media="(prefers-color-scheme: light)" srcset="https://raw.githubusercontent.com/CheshireMew/MediaFlow-Pro/star-history/star-history.svg">
  <img alt="MediaFlow Pro GitHub Star History" src="https://raw.githubusercontent.com/CheshireMew/MediaFlow-Pro/star-history/star-history.svg">
</picture>

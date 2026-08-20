# Third-party notices

MediaFlow Pro is licensed under GNU GPL v3. The components below are not relicensed by MediaFlow Pro; their original copyright and license terms continue to apply.

This source repository does not contain a generated portable runtime. When a portable runtime is explicitly requested, the release verification must regenerate the dependency inventory from the exact files being shipped and include the complete upstream license texts and notices next to those files.

## Direct runtime components

| Component | Locked/tested version | License | Upstream |
|---|---:|---|---|
| Python | 3.12.x | Python Software Foundation License | https://www.python.org/ |
| PySide6 / Qt for Python | 6.11.1 | LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only | https://doc.qt.io/qtforpython-6/ |
| Qt | 6.11.1 | GPL-3.0-only, LGPL-3.0-only, or commercial terms depending on module and distribution choice | https://www.qt.io/licensing/open-source-lgpl-obligations |
| ICU (Linux Qt build runtime) | 73.x | ICU License | https://icu.unicode.org/ |
| MLT Framework | 7.40.0 | Core libraries LGPL-2.1; melt and individual modules may use GPL or other compatible licenses | https://www.mltframework.org/docs/copyrightpolicy/ |
| FFmpeg | n8.1.2 tested runtime | GPLv3-or-later for the tested build because it is configured with `--enable-gpl --enable-version3` | https://ffmpeg.org/legal.html |
| yt-dlp | 2026.3.17 | Unlicense | https://github.com/yt-dlp/yt-dlp |
| Playwright for Python | 1.61.0 | Apache-2.0 | https://github.com/microsoft/playwright-python |
| OpenCV Python headless | 5.0.0.93 | Apache-2.0 | https://github.com/opencv/opencv-python |
| faster-whisper | 1.2.1 | MIT | https://github.com/SYSTRAN/faster-whisper |
| CTranslate2 | 4.8.1 | MIT | https://github.com/OpenNMT/CTranslate2 |
| OpenAI Python library | 2.45.0 | Apache-2.0 | https://github.com/openai/openai-python |
| Pydantic | 2.13.4 | MIT | https://github.com/pydantic/pydantic |
| aiohttp | 3.14.2 | Apache-2.0 AND MIT | https://github.com/aio-libs/aiohttp |
| psutil | 7.2.2 | BSD-3-Clause | https://github.com/giampaolo/psutil |
| MCP Python SDK / mcp-types | 2.0.0 | MIT | https://github.com/modelcontextprotocol/python-sdk |
| pywin32 (Windows only) | 312 | Python Software Foundation License | https://github.com/mhammond/pywin32 |
| json-repair | 0.61.4 | MIT | https://github.com/mangiucugna/json_repair |
| PyAV | 18.0.0 | BSD-3-Clause | https://github.com/PyAV-Org/PyAV |
| huggingface-hub | 1.23.0 | Apache-2.0 | https://github.com/huggingface/huggingface_hub |

## On-demand speaker identification runtime

The following components are downloaded only when the user chooses “Install Local Model”; they are stored under the configured MediaFlow runtime directory and are not part of the source repository or default application install.

| Component | Locked version/model | License | Upstream |
|---|---:|---|---|
| sherpa-onnx | 1.13.5 | Apache-2.0 | https://github.com/k2-fsa/sherpa-onnx |
| NumPy | 2.2.6 | BSD-3-Clause | https://numpy.org/ |
| 3D-Speaker CAM++ bilingual speaker embedding model | `speech_campplus_sv_zh_en_16k-common_advanced` | Apache-2.0 | https://modelscope.cn/models/iic/speech_campplus_sv_zh_en_16k-common_advanced |

## On-demand speaker diarization runtime

The setup script creates a separate environment under `MEDIAFLOW_DEV_ROOT`; these packages and the gated model are not included in the source repository or default application install. Access to the Community-1 model requires the user to accept its Hugging Face conditions and use their own access token. The model remains subject to CC-BY-4.0 attribution requirements after download.

| Component | Locked version/model | License | Upstream |
|---|---:|---|---|
| PyTorch | 2.11.0 | BSD-3-Clause | https://github.com/pytorch/pytorch |
| TorchAudio | 2.11.0 | BSD-2-Clause | https://github.com/pytorch/audio |
| pyannote.audio | 4.0.7 | MIT | https://github.com/pyannote/pyannote-audio |
| pyannote Community-1 speaker diarization model | `pyannote/speaker-diarization-community-1` | CC-BY-4.0 plus gated access conditions | https://huggingface.co/pyannote/speaker-diarization-community-1 |

## On-demand composite runtime archives

These optional archives are fetched directly from their upstream release locations only after the user selects installation. They are not approved for redistribution with MediaFlow Pro. A source repository license does not establish the rights for an archive that also contains third-party runtimes, libraries, or pretrained models.

| Component | Locked version | Archive license status | Distribution policy | Upstream |
|---|---:|---|---|---|
| Faster-Whisper XXL | r245.4 | NOASSERTION; the standalone release has no repository license file or complete contents notice | Upstream download only; bundling prohibited pending explicit permission and exact inventory | https://github.com/Purfview/whisper-standalone-win |
| GPT-SoVITS v2Pro Windows package | 20250604 | NOASSERTION for the composite archive; GPT-SoVITS source code is MIT, but the archive also contains a Python runtime, dependencies, and pretrained models | Upstream download only; bundling prohibited pending exact contents and rights review | https://github.com/RVC-Boss/GPT-SoVITS |

MLT modules and the tested Shotcut-provided multimedia runtime can load additional codec, audio, image, and GPU libraries. Those libraries must be inventoried from the final portable directory rather than inferred from the development machine.

## Embedded font

`mediaflow/resources/fonts/LXGWWenKai-Regular.ttf` is LXGW WenKai / 霞鹜文楷 and is distributed under the SIL Open Font License 1.1. Its required license text is included at `mediaflow/resources/fonts/OFL.txt`.

Upstream: https://github.com/lxgw/LxgwWenKai

## Embedded icons

MediaFlow Pro embeds a curated subset of Lucide Icons 1.33.0 as QML SVG path data. Lucide is distributed under the ISC License; some icons derived from Feather Icons are distributed under the MIT License. The complete required notice is included at `mediaflow/resources/icons/LUCIDE-LICENSE.txt`.

Upstream: https://github.com/lucide-icons/lucide

## Source availability

MediaFlow Pro source is available from https://github.com/CheshireMew/MediaFlow-Pro. For any future binary release, corresponding source and rebuild instructions for GPL-covered MediaFlow Pro code must remain available, and dynamically linked LGPL components must remain replaceable as required by their licenses.

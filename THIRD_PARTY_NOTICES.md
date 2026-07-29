# Third-party notices

MediaFlow Pro is licensed under GNU GPL v3. The components below are not relicensed by MediaFlow Pro; their original copyright and license terms continue to apply.

This source repository does not contain a generated portable runtime. When a portable runtime is explicitly requested, the release verification must regenerate the dependency inventory from the exact files being shipped and include the complete upstream license texts and notices next to those files.

## Direct runtime components

| Component | Locked/tested version | License | Upstream |
|---|---:|---|---|
| Python | 3.12.x | Python Software Foundation License | https://www.python.org/ |
| PySide6 / Qt for Python | 6.11.1 | LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only | https://doc.qt.io/qtforpython-6/ |
| Qt | 6.11.1 | GPL-3.0-only, LGPL-3.0-only, or commercial terms depending on module and distribution choice | https://www.qt.io/licensing/open-source-lgpl-obligations |
| MLT Framework | 7.40.0 | Core libraries LGPL-2.1; melt and individual modules may use GPL or other compatible licenses | https://www.mltframework.org/docs/copyrightpolicy/ |
| FFmpeg | n8.1.2 tested runtime | GPLv3-or-later for the tested build because it is configured with `--enable-gpl --enable-version3` | https://ffmpeg.org/legal.html |
| yt-dlp | 2026.3.17 | Unlicense | https://github.com/yt-dlp/yt-dlp |
| Playwright for Python | 1.61.0 | Apache-2.0 | https://github.com/microsoft/playwright-python |
| OpenCV Python headless | 5.0.0.93 | Apache-2.0 | https://github.com/opencv/opencv-python |
| faster-whisper | 1.2.1 | MIT | https://github.com/SYSTRAN/faster-whisper |
| CTranslate2 | 4.8.1 | MIT | https://github.com/OpenNMT/CTranslate2 |
| OpenAI Python library | 2.45.0 | Apache-2.0 | https://github.com/openai/openai-python |
| Pydantic | 2.13.4 | MIT | https://github.com/pydantic/pydantic |
| json-repair | 0.61.4 | MIT | https://github.com/mangiucugna/json_repair |
| PyAV | 18.0.0 | BSD-3-Clause | https://github.com/PyAV-Org/PyAV |
| huggingface-hub | 1.23.0 | Apache-2.0 | https://github.com/huggingface/huggingface_hub |

MLT modules and the tested Shotcut-provided multimedia runtime can load additional codec, audio, image, and GPU libraries. Those libraries must be inventoried from the final portable directory rather than inferred from the development machine.

## Embedded font

`mediaflow/resources/fonts/LXGWWenKai-Regular.ttf` is LXGW WenKai / 霞鹜文楷 and is distributed under the SIL Open Font License 1.1. Its required license text is included at `mediaflow/resources/fonts/OFL.txt`.

Upstream: https://github.com/lxgw/LxgwWenKai

## Source availability

MediaFlow Pro source is available from https://github.com/CheshireMew/MediaFlow-Pro. For any future binary release, corresponding source and rebuild instructions for GPL-covered MediaFlow Pro code must remain available, and dynamically linked LGPL components must remain replaceable as required by their licenses.

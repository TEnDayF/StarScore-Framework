# Third-party credits / 第三方署名

StarScore application code is GPL-3.0; see LICENSE. Model, library and plugin licenses are independent. This repository contains application/adapter code, not third-party runtimes or model weights. The Windows portable release additionally contains the core runtime listed below. Runtime license texts are inside the portable archive under licenses/ and each Python package's dist-info directory. Corresponding source materials are supplied as a separate asset on the **same Release**.

## Portable runtime

| Component / authors | Version | License / source |
|---|---|---|
| CPython / Python Software Foundation | 3.12.4 | PSF and bundled notices; https://www.python.org/downloads/release/python-3124/ |
| Qt / The Qt Company and contributors | 6.12.0 | LGPL-3.0 / GPL alternatives and module third-party notices; qtbase, qtmultimedia, qtsvg source archives in source-materials asset |
| PySide6 / Shiboken6 / The Qt Company | 6.12.0 | LGPL-3.0 / GPL alternatives; pyside-setup source archive in source-materials asset |
| NumPy contributors | 2.5.4 | BSD-3-Clause plus bundled OpenBLAS and runtime notices |
| SciPy developers | 1.18.1 | BSD-3-Clause plus bundled OpenBLAS and runtime notices |
| Mido / Ole Martin Bjørndalen and contributors | 1.3.3 | MIT; https://github.com/mido/mido |
| SoundFile / Bastian Bechtold, Matthias Geier and contributors | 0.14.0 | BSD-3-Clause; https://github.com/bastibe/python-soundfile |
| libsndfile / Erik de Castro Lopo and contributors | 1.2.2 | LGPL-2.1-or-later and bundled codec notices; locally rebuilt with external FLAC/Ogg/Opus/MPEG disabled; full source and build instructions provided |
| CFFI / Armin Rigo and contributors | 2.1.1 | MIT; package notices |
| pycparser / Eli Bendersky | 3.12 | BSD-3-Clause; package notices |
| packaging / Python Packaging Authority | 26.3 | Apache-2.0 or BSD-2-Clause; package notices |
| typing_extensions / Python contributors | 4.16.0 | PSF; package notices |
| Microsoft Visual C++ runtime / Microsoft | as contained in official Python/Qt wheels | Microsoft redistributable runtime; see https://learn.microsoft.com/en-us/cpp/windows/redistributing-visual-cpp-files |
| LLVM-MinGW runtime / LLVM and MinGW-w64 contributors | 20261104 UCRT | Toolchain notice retained with rebuilt libsndfile; Apache-2.0 with LLVM exception and component licenses |

Qt plugins in this portable build use Windows Media Foundation. No FFmpeg executable, FFmpeg plugin or avcodec/avformat/avutil/swscale/swresample library is included. Python and Qt binaries are dynamically loaded and can be replaced with compatible modified builds. There is no anti-tamper restriction on replacing those libraries; reverse engineering for debugging modifications to LGPL libraries is not prohibited by this application.

## Optional upstream projects — not bundled

- **MuScriptor**: Mirelo / Kyutai; GGUF conversion Damien Ronssin. CC BY-NC 4.0 model restrictions still apply to user-downloaded models. https://huggingface.co/DamRsn/muscriptor-gguf . Attribution and license retained under licenses/.
- **muscriptor.cpp**: Damien Ronssin and contributors; MIT. https://github.com/DamRsn/muscriptor.cpp . Native bridge source is included; the upstream library/binary is not.
- **ggml**: ggml authors; MIT. https://github.com/ggml-org/ggml . License retained for the optional native bridge.
- **Open-Unmix / UMXHQ**: Fabian-Robert Stöter / Antoine Liutkus and contributors. https://github.com/sigsep/open-unmix-pytorch ; weights record https://zenodo.org/records/3370489 . Not bundled.
- **Pedalboard**: Spotify AB and contributors, GPL-3.0. https://github.com/spotify/pedalboard . Optional rendering dependency, not bundled.
- **Vital**: Matt Tytel and contributors. https://vital.audio/ . Plugin, factory presets and wavetables are not bundled.
- **FL Studio**: Image-Line. https://www.image-line.com/ . Proprietary product, not bundled. No affiliation or endorsement is claimed.
- **FFmpeg**: FFmpeg developers. https://ffmpeg.org/download.html . User-selected external tool, not bundled.

No personal music, FL projects, model weights, factory preset templates or personal timbre libraries are included. Historical withdrawn packages are not republished; this release does not make a claim that historical distribution obligations have disappeared.

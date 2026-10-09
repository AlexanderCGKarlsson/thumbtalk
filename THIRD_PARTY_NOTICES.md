# Third-party notices and licensing status

## ThumbTalk and GamepadSpeak

ThumbTalk started from [GamepadSpeak by kubeden](https://github.com/kubeden/gps).
This repository starts with a fresh snapshot and retains origin credit here.
The two previously unchanged source launchers
have been replaced with launchers for the locked ThumbTalk environment. The
companion and addon are substantially rewritten. A bounded source comparison
against upstream revision `fb6f1bb747b66c8f3c2e9793a87a1bc532d61862` found no
matching blocks at its six-line/120-character threshold after ignoring comments
and whitespace. This is not proof of independent authorship or legal clearance.
The upstream repository currently has no
project license. Credit alone does not establish redistribution permission;
permission and applicable terms remain unresolved. No project-wide open-source
license has been assigned here.
Do not infer one from a bundled dependency's license.

## Referenced projects

[Decktation by silverfoxy](https://github.com/silverfoxy/decktation) informed the
Linux clipboard/virtual-keyboard approach and sending tests. ThumbTalk's
integration was implemented independently; no Decktation source is bundled.
[ConsolePort](https://www.curseforge.com/wow/addons/console-port) inspired the
controller presentation; ThumbTalk uses its own vector symbols and does not
bundle ConsolePort or Blizzard artwork. No endorsement is implied.

## Runtime dependencies

Release builds collect installed Python-package metadata and available license,
notice and copyright files into `THIRD_PARTY/` alongside the executable. The
inventory records exact versions from the build environment and flags packages
whose wheels contain no notice files. It includes build tools conservatively;
being listed does not mean every module is loaded or shipped. This collection is
an audit aid, not a replacement license or a declaration that all distribution
obligations are satisfied.

Notable components include Python, PySide6/Qt and Shiboken, pygame-ce/SDL,
sounddevice/PortAudio, faster-whisper, CTranslate2, PyAV/FFmpeg, sherpa-onnx and
ONNX Runtime, pynput, and python-xlib. Wheels may contain additional native
libraries with separate notices or source obligations. Before publishing, review
the exact Windows and Linux inventories, fill missing upstream notices and make
required corresponding source/rebuild materials available for the shipped
versions. In particular, audit the Qt, SDL and FFmpeg libraries actually bundled.

Linux includes separate command-line tools:

- [ydotool](https://github.com/ReimuNotMoe/ydotool): GNU AGPLv3. Unmodified source,
  license and rebuild script are in `packaging/third-party/ydotool/` and
  `packaging/build-linux-input.sh`; bundles include them under `_internal/input/source/`.
- [xclip](https://github.com/astrand/xclip): GPL. Corresponding Ubuntu 0.13-2 source,
  Debian packaging and notices are in `packaging/third-party/xclip/`; bundles
  include them under `_internal/clipboard/source/` and dependency notices under
  `_internal/clipboard/licenses/`.

## Downloaded speech models

Models are downloaded separately, not embedded in the installer.

- [OpenAI Whisper](https://github.com/openai/whisper) provides the original models
  under MIT terms. ThumbTalk uses [faster-whisper](https://github.com/SYSTRAN/faster-whisper)
  and its CTranslate2 conversions.
- [NVIDIA Parakeet TDT 0.6B V3](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3)
  is licensed under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).
  ThumbTalk uses the [INT8 ONNX conversion by csukuangfj](https://huggingface.co/csukuangfj/sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8).
  Conversion/quantization changes the representation. NVIDIA and the converter
  do not endorse ThumbTalk.

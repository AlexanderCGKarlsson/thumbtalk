# Privacy and local data

ThumbTalk processes speech on your device. It does not send recordings or
transcripts to a transcription API, and it has no ThumbTalk account or analytics
service.

## While it is running

The microphone remains open during an active companion session so recording can
start promptly. Frames outside an active recording are discarded. Recordings
used by the voice test stay in memory until replaced or the test stops. Review
text appears locally in WoW's chat history. On acceptance, or in immediate mode,
WoW sends the message to the selected in-game conversation.

Linux text delivery temporarily uses the system clipboard. Clipboard managers,
clipboard-history tools or other applications with clipboard access may retain
that text. Transient clipboard cleanup cannot delete another app's history.

## Local files

Preferences include microphone and controller choices, WoW locations, languages,
models, channels and any saved whisper recipients. Settings live at
`%APPDATA%\thumbtalk\settings.json` on Windows or
`${XDG_CONFIG_HOME:-~/.config}/thumbtalk/settings.json` on Linux.
Downloaded models are cached locally. Setup and companion logs may contain device
names, game paths, diagnostic context and error details. Command-line transcription
prints its result, and optional diagnostic tools produce local reports.

No report is uploaded automatically. Check logs, screenshots and diagnostic
archives before sharing; remove private chat, character names and account paths.
Do not submit a full settings JSON unless you have reviewed its contents.

## Network access

- Models are downloaded from Hugging Face when requested. Model requests disclose
  ordinary connection information to that service, such as your IP address and
  download client details. Speech processing stays local afterward.
- **Check for updates** requests public release metadata from GitHub only when
  selected. No audio, transcripts or saved preferences are included.
- Installers fetch app packages from GitHub unless a complete offline package is
  supplied. Opening documentation or support links uses your browser normally.

[Report a privacy or security concern privately](https://github.com/AlexanderCGKarlsson/thumbtalk/security/advisories/new).

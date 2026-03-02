# CCE-Audio

## Packaging for Codex boot compatibility

Use `package_release.sh` to build a release ZIP without nesting the final folder inside the archive.

```bash
./package_release.sh
```

The script expects your final prepared contents in:

- `CCE_Audio_Main_P16_100pct_TRUE_READY_BOOT/`

It then copies that directory's contents into a temporary `build/` directory and creates:

- `CCE_Audio_Main_P16_100pct_TRUE_READY_BOOT_YYYYMMDD.zip`
# CCE Audio Studio — KRSTD Boot (Single Path)

Use **ONE** of these (both work):

## Option A (recommended)
- `00_setup.bat`
- `_run.bat`
- `_open_browser.bat`
- `_run_tests.bat`

## Option B (`_BAT` wrappers)
- `_BAT\1_setup.bat`
- `_BAT\2_run.bat`
- `_BAT\3_open_browser.bat`
- `_BAT\6_run_tests.bat`

If the server fails to start, the **"CCE Audio Server"** window will remain open and show the real Python error.

---

# What this engine does

Internal CC Engine that generates **audio production packs** (arrangement + timeline + stems plan + mix/master plan + platform templates) for Studio Hubs.

**Workflow:** Hub order ➜ pack generation ➜ critique + approval gate ➜ export ZIP ➜ hub ingests/schedules/publishes.

---

# URLs

- Home: `http://127.0.0.1:5204/`
- Diagnostics: `http://127.0.0.1:5204/diagnostics`
- Ready Stamp: `http://127.0.0.1:5204/ready`
- API spec: `http://127.0.0.1:5204/api/spec`

---

# Notes

- OpenAI features are **optional**. If `OPENAI_API_KEY` is not set (or `openai` is not installed), the engine runs **offline-first**.
- Upload analysis is best-effort:
  - WAV uses built-in decoding.
  - MP3/MP4/etc. needs `ffprobe` (ffmpeg) installed and on PATH.


## P11 Audio Utility Layer

- Waveform preview + click-to-place markers (project page)
- Server tools (WAV): trim + segment pack ZIP
- Deliverables defaults added into export ZIP under `platforms/deliverables/`

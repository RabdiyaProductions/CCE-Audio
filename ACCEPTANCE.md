# ACCEPTANCE — True Ready checklist (CCE Audio Studio)

## A) Boot & Determinism
- [ ] Fresh setup works via `00_setup.bat` (no manual fixes)
- [ ] `_run.bat` starts server window and auto-checks `/health`
- [ ] `_open_browser.bat` opens `/ready`
- [ ] Port is read from `meta.json`

## B) Functional Acceptance (Critical Path)
- [ ] Create a project
- [ ] Generate pilot pack
- [ ] Generate sonic branding pack
- [ ] Generate podcast pack (if relevant)
- [ ] Generate voiceover pack (if relevant)
- [ ] Generate score cue pack (if relevant)
- [ ] Generate SFX pack (if relevant)
- [ ] Approve
- [ ] Export ZIP
- [ ] Download ZIP and verify required files exist

## C) Regression Tripwire
- [ ] `tools/run_full_tests.py` passes
- [ ] Static checks pass when `CODEX_MODE=1`

## D) Error Handling
- [ ] Missing `project_code` returns 400 with clear error
- [ ] Export without approval returns 400 (unless force=true)

## E) Data & Persistence
- [ ] Projects persist in SQLite
- [ ] Assets persist and are viewable
- [ ] Exports/Imports registries persist

## F) Security & Privacy
- [ ] No secrets committed
- [ ] `OPENAI_API_KEY` only via env

## G) Packaging / Deployment
- [ ] ZIP export is deterministic and includes `manifest.json`

## H) Ready Stamp
- [ ] `/ready` shows version + health + last smoke run snapshot

### P11 Acceptance Addendum — Audio Utility

1. On a project page, load a local WAV in **Audio Utility**.
2. Waveform renders; clicking adds markers; markers list updates.
3. **Server Trim** returns a downloadable WAV and playable preview.
4. **Segment Pack ZIP** returns a zip containing `segments/*.wav`, `stems_index.csv`, and `segments_manifest.json`.
5. Export ZIP contains `platforms/deliverables/` files.

### P12–P16 Acceptance Addendum — Multi-format + Loudness QC + Integrations

1. (Optional) Set ffmpeg paths in **Settings** if not on PATH.
2. In **Audio Utility**, upload MP3/M4A/WAV:
   - Trim works (non-WAV requires ffmpeg).
   - Segment pack works (non-WAV requires ffmpeg).
3. Run **Loudness QC**:
   - QC report returns with measured LUFS + true peak.
   - QC report is stored as asset `loudness_qc`.
4. Export ZIP includes `qc/loudness_qc_report.json` once QC has been run.
5. Run `_run_tests.bat`:
   - `/ready` shows last smoke run snapshot (file or DB).

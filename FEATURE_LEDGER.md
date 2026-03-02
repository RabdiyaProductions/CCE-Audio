# FEATURE_LEDGER — CCE Audio Studio

## App / Node Identity
- Name: CCE Audio Studio
- Node type: CCE Engine (internal factory)
- Key: `cce.audio.studio.mvp5`
- Version: `P16-100pct`
- Primary UI: `/` ➜ Projects ➜ Project View
- Primary API: `/api/spec`

---

## Critical Path (Top 3 journeys)
1) **Create project ➜ Generate pilot pack ➜ Approve ➜ Export ZIP**
2) **Iterate: timeline edit ➜ regenerate section ➜ producer notes / critique ➜ export**
3) **Hub order: `/api/hub/order` ➜ worker processes ➜ `/api/hub/result/<id>` provides export**

---

## Feature Ledger
| ID | Feature | UI Entry Point | Backend Endpoint | Data Store | Output / Artifact | Acceptance Check | Status |
|---|---|---|---|---|---|---|---|
| F-001 | Boot-safe setup (venv + deps) | BAT scripts | n/a | n/a | Deterministic run | Setup succeeds on fresh clone | ✅ |
| F-002 | Health + Version | Ready/Diagnostics | `/health`, `/version` | SQLite | JSON | 200 + status ok | ✅ |
| F-003 | Project CRUD (create + view) | New Project Wizard | `/projects/new`, `/api/orders` | SQLite | project row | Project visible in list | ✅ |
| F-004 | Pilot pack generation | Project View | `/api/generate/pilot` | Assets table | `pilot_pack` asset | pilot_pack present in assets | ✅ |
| F-005 | Timeline update | Project View (API) | `/api/timeline/update` | Assets | new pilot_pack revision | timeline updated saved | ✅ |
| F-006 | Regenerate section | Project View (API) | `/api/timeline/regenerate` | Assets | regen_event asset | updated_clip returned | ✅ |
| F-007 | Producer notes | Project View | `/api/agents/director` | Assets | producer_notes asset | returns JSON | ✅ |
| F-008 | Critique | Project View | `/api/critic/run` | Assets | critic_report asset | returns JSON (or offline note) | ✅ |
| F-009 | Approval gate | Project View | `/api/approve` | Projects + Assets | approval asset | status becomes Approved | ✅ |
| F-010 | Export ZIP + manifest | Project View / Exports | `/api/export` | Exports registry | export ZIP | zip contains required files | ✅ |
| F-011 | Import registry | Imports page | `/api/import` | Imports registry | stored file | appears in imports list | ✅ |
| F-012 | Hub handshake jobs | Jobs page | `/api/hub/order` etc | Jobs table | job export ZIP | job completes + download URL | ✅ |
| F-013 | Audio upload analysis | Project View | `/api/upload/audio` | uploads + assets | uploaded_audio asset | analysis returned | ✅ |
| F-014 | ZoomBot transcript ingest (stub) | API | `/api/zoombot/ingest` | Assets | zoombot_ingest asset | returns summary JSON | ✅ |
| F-015 | Avatar profile generator (stub) | API | `/api/avatar/profile` | Assets | avatar_profile asset | returns persona JSON | ✅ |
| F-016 | Sonic branding pack generator | Project View | `/api/generate/sonic_brand` | Assets table | `sonic_brand_pack` JSON | returns JSON + exported in ZIP | ✅ |
| F-017 | Podcast pack generator | Project View | `/api/generate/podcast_pack` | Assets table | `podcast_pack` JSON | returns JSON + exported in ZIP | ✅ |
| F-018 | Voiceover pack generator | Project View | `/api/generate/voice_pack` | Assets table | `voice_pack` JSON | returns JSON + exported in ZIP | ✅ |
| F-019 | Score cue pack generator | Project View | `/api/generate/score_cue_pack` | Assets table | `score_cue_pack` JSON | returns JSON + exported in ZIP | ✅ |
| F-020 | SFX pack generator | Project View | `/api/generate/sfx_pack` | Assets table | `sfx_pack` JSON | returns JSON + exported in ZIP | ✅ |

## P11 Audio Utility Layer

- UI: waveform preview, click-to-place markers, trim + segment pack tools (project page)
- API:
  - `POST /api/audio/trim` (multi-format; non-WAV converts via ffmpeg if available)
  - `POST /api/audio/segment_pack` (multi-format; markers_json)
  - `GET /media/exports/<filename>` (streamable export files)
- Export ZIP:
  - `platforms/deliverables/` includes naming conventions, loudness defaults, QC checklist, stems index

## P12–P16 Multi-format + Loudness QC + Integrations

- Loudness QC:
  - `GET /api/audio/qc/targets`
  - `POST /api/audio/qc` (ffmpeg ebur128 if available; WAV RMS proxy fallback)
  - Export ZIP includes `qc/loudness_qc_report.json` when QC has been run.

- Settings + guardrails:
  - UI: `/settings`
  - API: `GET/POST /api/settings`
  - Stored in SQLite settings table.
  - Best-effort API rate limiting for `/api/*`.

- Webhooks:
  - Optional `webhook_export_url` fired on export completion (best-effort; never blocks export).

- Spine upgrades:
  - Schema v3: settings + smoke_runs + blob scaffolding.
  - Smoke run records written by `tools/run_full_tests.py` and shown in `/ready`.

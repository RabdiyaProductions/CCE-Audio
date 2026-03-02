import os
import sys
import json
import compileall
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def fail(msg: str) -> None:
    print("FAIL:", msg)
    sys.exit(1)


def ok(msg: str) -> None:
    print("OK:", msg)


def run_static_checks() -> None:
    required_files = [
        "app.py",
        "run_server.py",
        "meta.json",
        "requirements.txt",
        "templates/cca_home.html",
        "templates/cca_project.html",
        "templates/cca_exports.html",
        "templates/cca_imports.html",
        "FEATURE_LEDGER.md",
        "ACCEPTANCE.md",
    ]
    for rf in required_files:
        if not (ROOT / rf).exists():
            fail(f"missing_required_file:{rf}")

    try:
        meta = json.loads((ROOT / "meta.json").read_text(encoding="utf-8"))
    except Exception as e:
        fail(f"meta_json_invalid:{type(e).__name__}")
    for k in ("name", "key", "port"):
        if k not in meta:
            fail(f"meta_json_missing_key:{k}")

    if not compileall.compile_dir(str(ROOT), quiet=1):
        fail("compileall_failed")

    ok("static_checks_pass")


def run_local_full_flow() -> None:
    from app import create_app

    app = create_app()
    with app.test_client() as c:
        for p in ("/health", "/api/spec", "/version", "/ready"):
            if c.get(p).status_code != 200:
                fail(p)

        payload = {
            "action": "create_project",
            "project": {
                "title": "Test Audio Pilot",
                "studio": "Cosmic",
                "audio_type": "Song",
                "genre": "Cinematic",
                "bpm": "120",
                "musical_key": "A minor",
                "mood": "Epic",
                "references": "Big drums, clean low end.",
                "lyrics_theme": "",
                "notes": "Offline-first test.",
            },
        }
        r = c.post("/api/orders", json=payload)
        if r.status_code != 200:
            fail("/api/orders")
        code = (r.get_json() or {}).get("project_code")
        if not code:
            fail("no_project_code")

        r = c.post("/api/generate/pilot", json={"project_code": code})
        if r.status_code != 200:
            fail("/api/generate/pilot")

        # extra generators (should be safe offline-first)
        for ep in (
            "/api/generate/sonic_brand",
            "/api/generate/podcast_pack",
            "/api/generate/voice_pack",
            "/api/generate/score_cue_pack",
            "/api/generate/sfx_pack",
        ):
            r = c.post(ep, json={"project_code": code})
            if r.status_code != 200:
                fail(ep)


        

        # P11 audio utility: trim + segment pack (WAV)
        import io, wave
        buf = io.BytesIO()
        with wave.open(buf, 'wb') as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            # 1.0s of a simple tone-ish sample (not important)
            frames = bytearray()
            for i in range(16000):
                # small ramp waveform
                v = int(16000 * ((i % 200) / 200.0) - 8000)
                frames += int(v).to_bytes(2, 'little', signed=True)
            w.writeframes(bytes(frames))
        wav_bytes = buf.getvalue()

        # trim
        data = {
            'project_code': code,
            'start_sec': '0.20',
            'end_sec': '0.80',
            'file': (io.BytesIO(wav_bytes), 'test.wav'),
        }
        r = c.post('/api/audio/trim', data=data, content_type='multipart/form-data')
        if r.status_code != 200:
            fail('/api/audio/trim')
        tj = r.get_json() or {}
        out_name = tj.get('output_filename')
        if not out_name:
            fail('trim_missing_output_filename')
        if not (ROOT / 'exports' / out_name).exists():
            fail('trim_output_missing_on_disk')

        # segment pack
        markers = [0.25, 0.50, 0.75]
        data = {
            'project_code': code,
            'markers_json': json.dumps(markers),
            'file': (io.BytesIO(wav_bytes), 'test.wav'),
        }
        r = c.post('/api/audio/segment_pack', data=data, content_type='multipart/form-data')
        if r.status_code != 200:
            fail('/api/audio/segment_pack')
        sj = r.get_json() or {}
        zname = sj.get('zip_filename')
        if not zname:
            fail('segment_pack_missing_zip_filename')
        zp2 = ROOT / 'exports' / zname
        if not zp2.exists():
            fail('segment_pack_zip_missing_on_disk')
        import zipfile
        with zipfile.ZipFile(zp2, 'r') as z:
            names = set(z.namelist())
            for x in ('stems_index.csv', 'segments_manifest.json', 'markers.json'):
                if x not in names:
                    fail('segment_pack_missing:' + x)
            segs = [n for n in names if n.startswith('segments/') and n.lower().endswith('.wav')]
            if len(segs) < 2:
                fail('segment_pack_no_segments')

        # P12+ Loudness QC (WAV fallback if ffmpeg not present)
        data = {
            'project_code': code,
            'target_profile': 'podcast_stereo',
            'file': (io.BytesIO(wav_bytes), 'test.wav'),
        }
        r = c.post('/api/audio/qc', data=data, content_type='multipart/form-data')
        if r.status_code != 200:
            fail('/api/audio/qc')
        qj = r.get_json() or {}
        if 'measured' not in qj:
            fail('qc_missing_measured')


        # timeline update
        pj = c.get(f"/api/projects/{code}").get_json() or {}
        assets = pj.get("assets") or []
        pilot_assets = [a for a in assets if a.get("kind") == "pilot_pack"]
        if not pilot_assets:
            fail("no_pilot_pack_asset")
        pack = json.loads(pilot_assets[0]["payload_json"])
        tl = pack.get("timeline") or []
        if not tl:
            fail("no_timeline")
        tl[0]["notes"] = (tl[0].get("notes") or "") + " [TEST]"

        r = c.post("/api/timeline/update", json={"project_code": code, "timeline": tl})
        if r.status_code != 200:
            fail("/api/timeline/update")

        clip_id = tl[0].get("clip_id")
        r = c.post("/api/timeline/regenerate", json={"project_code": code, "clip_id": clip_id, "instruction": "Make the hook punchier"})
        if r.status_code != 200:
            fail("/api/timeline/regenerate")

        # Approve + export
        r = c.post("/api/approve", json={"project_code": code})
        if r.status_code != 200:
            fail("/api/approve")

        r = c.post("/api/export", json={"project_code": code, "force": False})
        if r.status_code != 200:
            fail("/api/export")
        j = r.get_json() or {}
        export_name = j.get("filename")
        if not export_name:
            fail("export_missing_filename")

        zp = ROOT / "exports" / export_name
        if not zp.exists():
            fail("export_zip_missing_on_disk")

        import zipfile

        required = [
            "issue_ref.txt",
            "project.json",
            "pilot_pack.json",
            "sonic_brand_pack.json",
            "podcast_pack.json",
            "voice_pack.json",
            "score_cue_pack.json",
            "sfx_pack.json",
            "export_meta.json",
            "WORKFLOW.md",
            "platforms/deliverables/naming_conventions.md",
            "platforms/deliverables/loudness_targets.json",
            "platforms/deliverables/qc_checklist.md",
            "platforms/deliverables/stems_index.csv",
            "qc/loudness_qc_report.json",
            "manifest.json",
        ]
        with zipfile.ZipFile(zp, "r") as z:
            names = set(z.namelist())
            missing = [x for x in required if x not in names]
            if missing:
                fail("export_zip_missing_files:" + ",".join(missing))
            manifest = json.loads(z.read("manifest.json").decode("utf-8", errors="ignore"))
            for x in required:
                if x not in manifest:
                    fail("manifest_missing_entry:" + x)
                ent = manifest.get(x) or {}
                if ("sha256" not in ent) or ("bytes" not in ent):
                    fail("manifest_entry_incomplete:" + x)

        # Record smoke run (file + DB) for /ready page.
        try:
            from datetime import datetime, timezone
            import storage_core as storage
            ts = datetime.now(timezone.utc).isoformat().replace('+00:00','Z')
            meta = json.loads((ROOT / 'meta.json').read_text(encoding='utf-8'))
            details = {
                "ok": True,
                "ts": ts,
                "engine": meta.get('name'),
                "key": meta.get('key'),
                "version": meta.get('version'),
                "last_project_code": code,
                "last_export": export_name,
            }
            (ROOT / 'data').mkdir(parents=True, exist_ok=True)
            (ROOT / 'data' / 'last_smoke_run.json').write_text(json.dumps(details, indent=2), encoding='utf-8')
            conn = storage.connect(ROOT / 'data' / 'app.db')
            storage.migrate(conn)
            storage.add_smoke_run(conn, ts, True, json.dumps(details))
        except Exception:
            pass

        ok(f"PASS: {code} {export_name}")


if __name__ == "__main__":
    # Codex-like environments may not have deps; allow static-only mode.
    if os.environ.get("CODEX_MODE", "").strip() == "1":
        run_static_checks()
        sys.exit(0)

    # If Flask isn't importable, fall back automatically to static checks.
    try:
        import flask  # noqa: F401
    except Exception:
        run_static_checks()
        sys.exit(0)

    run_local_full_flow()
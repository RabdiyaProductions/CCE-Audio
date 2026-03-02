import os, json, time
from pathlib import Path
from datetime import datetime, timezone

from flask import Flask, request, jsonify, render_template, redirect, url_for, send_from_directory, abort

from cce_audio_core import (
    ensure_dirs, DEFAULT_QUESTIONS, parse_project_spec, build_pilot_pack, build_sonic_brand_pack, build_podcast_pack, build_voice_pack, build_score_cue_pack, build_sfx_pack,
    render_platform_packs, make_issue_ref, safe_filename, sha256_bytes, analyze_audio_file,
    wav_trim, wav_split_by_markers, stems_index_csv_from_segments,
    default_loudness_targets, ensure_audio_wav, loudness_qc_report, ffmpeg_available, ffprobe_available
)
import storage_core as storage
from llm_core import LLMProvider
from job_worker import JobWorker

APP_BASE = Path(__file__).resolve().parent
META_PATH = APP_BASE / "meta.json"


def load_meta():
    if META_PATH.exists():
        return json.loads(META_PATH.read_text(encoding="utf-8"))
    return {"port": 5204, "name": "CCE Audio"}


def _slug(s: str) -> str:
    import re
    s = (s or "").strip().lower().replace(".", "-").replace(" ", "_")
    s = re.sub(r"[^a-z0-9_\-]", "-", s)
    s = re.sub(r"[-_]+", "-", s).strip("-_")
    return s or "project"


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def create_app():
    meta = load_meta()
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.config["JSON_SORT_KEYS"] = False
    app.secret_key = os.environ.get("FLASK_SECRET_KEY", "cce-audio-dev-key")

    dirs = ensure_dirs(APP_BASE)
    db_path = dirs["data"] / "app.db"
    conn = storage.connect(db_path)
    storage.migrate(conn)

    llm = LLMProvider()

    # Background worker for Hub orders (optional)
    worker = JobWorker(conn, APP_BASE, llm=llm)
    if os.environ.get("DISABLE_WORKER", "").strip() != "1":
        worker.start()

    # ----------------- Runtime settings + rate limiting -----------------
    _rate = {"hits": {}}  # ip -> [timestamps]

    def _get_setting(k: str, default: str | None = None) -> str | None:
        try:
            return storage.get_setting(conn, k, default)
        except Exception:
            return default

    def _apply_runtime_settings() -> None:
        # Optional overrides for external binaries.
        ffmpeg_path = (_get_setting("ffmpeg_path") or "").strip()
        ffprobe_path = (_get_setting("ffprobe_path") or "").strip()
        if ffmpeg_path:
            os.environ["CCE_FFMPEG"] = ffmpeg_path
        if ffprobe_path:
            os.environ["CCE_FFPROBE"] = ffprobe_path

    @app.before_request
    def _before_request_guard():
        _apply_runtime_settings()
        # Basic API rate limiting (best-effort; never blocks UI pages).
        if not request.path.startswith("/api/"):
            return None
        # allow health checks freely
        if request.path in ("/api/spec", "/api/llm/status", "/health", "/version"):
            return None

        rps_raw = (_get_setting("api_rps", "25") or "25").strip()
        try:
            limit = int(max(5, float(rps_raw)))
        except Exception:
            limit = 25

        ip = request.headers.get("X-Forwarded-For", "").split(",")[0].strip() or (request.remote_addr or "local")
        now = time.time()
        arr = _rate["hits"].get(ip) or []
        arr = [t for t in arr if (now - t) <= 1.0]
        if len(arr) >= limit:
            return jsonify({"ok": False, "error": "rate_limited", "hint": f"Too many requests. Limit ~{limit}/sec."}), 429
        arr.append(now)
        _rate["hits"][ip] = arr
        return None

    @app.context_processor
    def _inject_globals():
        return {"llm": llm.status(), "meta": meta}

    def app_spec():
        return {
            "name": meta.get("name", "CCE Audio Studio"),
            "key": meta.get("key", "cce.audio.studio.mvp5"),
            "version": meta.get("version", "P9"),
            "port": meta.get("port", 5204),
            "routes": sorted([r.rule for r in app.url_map.iter_rules()]),
            "internal_only": True,
            "lane": "CCE Engine (private factory)",
        }

    def _audit(kind: str, payload: dict, project_code: str | None = None):
        try:
            storage.audit(conn, kind, json.dumps(payload, ensure_ascii=False), now_utc(), project_code=project_code)
        except Exception:
            pass

    def _get_latest_pack(project_code: str) -> dict | None:
        assets = storage.list_assets(conn, project_code, kind="pilot_pack", limit=1)
        if not assets:
            return None
        try:
            return json.loads(assets[0]["payload_json"])
        except Exception:
            return None

    def _asset_filename(asset_row: dict) -> str:
        try:
            payload = json.loads(asset_row.get("payload_json") or "{}")
        except Exception:
            payload = {}
        return (payload.get("filename") or payload.get("output") or "").strip()

    def _asset_is_wav(asset_row: dict) -> bool:
        fn = _asset_filename(asset_row).lower()
        return fn.endswith(".wav")


    def _fire_export_webhook(event: dict) -> None:
        url = (_get_setting("webhook_export_url") or "").strip()
        if not url:
            return
        try:
            import urllib.request
            data = json.dumps(event, ensure_ascii=False).encode("utf-8")
            req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
            urllib.request.urlopen(req, timeout=4).read()
        except Exception:
            # never block exports
            return

    # ----------------- Contract endpoints -----------------
    @app.get("/health")
    def health():
        db_ok = True
        try:
            conn.execute("SELECT 1").fetchone()
        except Exception:
            db_ok = False
        return jsonify({
            "status": "ok",
            "engine": "cc_audio",
            "name": meta.get("name", "CCE Audio Studio"),
            "key": meta.get("key", "cce.audio.studio.mvp5"),
            "version": meta.get("version", "P9"),
            "port": meta.get("port", 5204),
            "time_utc": now_utc(),
            "db_ok": db_ok,
            "llm": llm.status(),
        })

    @app.get("/version")
    def version():
        return jsonify({
            "name": meta.get("name", "CCE Audio Studio"),
            "key": meta.get("key", "cce.audio.studio.mvp5"),
            "version": meta.get("version", "P9"),
            "port": meta.get("port", 5204),
            "time_utc": now_utc(),
        })

    @app.get("/api/spec")
    def api_spec():
        return jsonify(app_spec())

    @app.get("/api/llm/status")
    def llm_status():
        return jsonify(llm.status())

    @app.get("/api/state")
    def api_state():
        # lightweight snapshot
        return jsonify({
            "meta": meta,
            "projects": storage.list_projects(conn, limit=50),
            "exports": storage.list_exports(conn, limit=20),
            "imports": storage.list_imports(conn, limit=20),
        })

    # ----------------- UI routes -----------------
    @app.get("/")
    def home():
        projects = storage.list_projects(conn, limit=50)
        return render_template("cca_home.html", projects=projects)

    @app.get("/projects")
    def projects_page():
        projects = storage.list_projects(conn, limit=500)
        return render_template("cca_projects.html", projects=projects)

    @app.get("/projects/new")
    def new_project_page():
        return render_template("cca_new_project.html", questions=DEFAULT_QUESTIONS)

    @app.post("/projects/new")
    def new_project_submit():
        form = request.form.to_dict()
        spec = parse_project_spec(form)
        code = (_slug(spec.title)[:24]).upper()
        now = now_utc()
        storage.upsert_project(conn, {
            "project_code": code,
            "title": spec.title,
            "studio": spec.studio,
            "audio_type": spec.audio_type,
            "genre": spec.genre,
            "bpm": spec.bpm,
            "musical_key": spec.musical_key,
            "mood": spec.mood,
            "reference_notes": spec.references,
            "lyrics_theme": spec.lyrics_theme,
            "notes": spec.notes,
            "status": "Draft",
            "created_at": now,
            "updated_at": now,
        })
        _audit("project_create", {"title": spec.title}, project_code=code)
        return redirect(url_for("project_view", code=code))

    @app.get("/projects/<code>")
    def project_view(code):
        p = storage.get_project(conn, code)
        if not p:
            abort(404)
        assets = storage.list_assets(conn, code, limit=200)
        exports = storage.list_exports(conn, code, limit=100)
        mix_buses = storage.list_mix_buses(conn, code)
        wav_assets = []
        for a in assets:
            if _asset_is_wav(a):
                wav_assets.append({
                    "id": a.get("id"),
                    "kind": a.get("kind"),
                    "created_at": a.get("created_at"),
                    "filename": _asset_filename(a),
                })
        timelines = storage.list_timelines(conn, code)
        return render_template("cca_project.html", project=p, assets=assets, exports=exports, mix_buses=mix_buses, wav_assets=wav_assets, timelines=timelines)

    @app.get("/imports")
    def imports_page():
        imports = storage.list_imports(conn, limit=200)
        return render_template("cca_imports.html", imports=imports)

    @app.get("/exports")
    def exports_page():
        exports = storage.list_exports(conn, limit=200)
        return render_template("cca_exports.html", exports=exports)

    @app.get("/export-import")
    def export_import_page():
        exports = storage.list_exports(conn, limit=200)
        imports = storage.list_imports(conn, limit=200)
        return render_template("cca_export_import.html", exports=exports, imports=imports)

    @app.get("/diagnostics")
    def diagnostics():
        # simple checks
        results = {"ok": True, "checks": []}

        def chk(name, fn):
            try:
                fn()
                results["checks"].append({"name": name, "ok": True})
            except Exception as e:
                results["ok"] = False
                results["checks"].append({"name": name, "ok": False, "error": str(e)})

        chk("db_select", lambda: conn.execute("SELECT 1").fetchone())
        chk("projects_list", lambda: storage.list_projects(conn, limit=1))
        chk("llm_status", lambda: llm.status())

        return render_template("cca_diagnostics.html", results=results)

    @app.get("/ready")
    def ready():
        last_path = dirs["data"] / "last_smoke_run.json"
        last = None
        try:
            if last_path.exists():
                last = json.loads(last_path.read_text(encoding="utf-8"))
        except Exception:
            last = {"error": "could_not_read_last_smoke_run"}

        health_json = {}
        try:
            with app.test_client() as c:
                health_json = (c.get("/health").get_json() or {})
        except Exception as e:
            health_json = {"error": str(e)}

        # Prefer DB smoke run if available
        try:
            db_last = storage.get_latest_smoke_run(conn)
            if db_last and db_last.get("details_json"):
                try:
                    last = json.loads(db_last["details_json"])
                except Exception:
                    pass
        except Exception:
            pass

        return render_template("cca_ready.html", last=last, health=health_json)

    @app.get("/settings")
    def settings_page():
        items = {s["k"]: s for s in (storage.list_settings(conn) or [])}
        view = {
            "ffmpeg_path": (items.get("ffmpeg_path") or {}).get("v", ""),
            "ffprobe_path": (items.get("ffprobe_path") or {}).get("v", ""),
            "webhook_export_url": (items.get("webhook_export_url") or {}).get("v", ""),
            "api_rps": (items.get("api_rps") or {}).get("v", "25"),
        }
        return render_template(
            "cca_settings.html",
            settings=view,
            binaries={"ffmpeg": ffmpeg_available(), "ffprobe": ffprobe_available()},
        )

    @app.post("/settings")
    def settings_submit():
        form = request.form.to_dict()
        now = now_utc()
        for k in ("ffmpeg_path", "ffprobe_path", "webhook_export_url", "api_rps"):
            if k in form:
                storage.set_setting(conn, k, (form.get(k) or "").strip(), now)
        _audit("settings_update", {"keys": list(form.keys())}, project_code=None)
        return redirect(url_for("settings_page"))

    @app.get("/api/settings")
    def api_get_settings():
        items = storage.list_settings(conn) or []
        out = {x["k"]: x.get("v") for x in items}
        out["binaries"] = {"ffmpeg": ffmpeg_available(), "ffprobe": ffprobe_available()}
        return jsonify({"ok": True, "settings": out})

    @app.post("/api/settings")
    def api_set_settings():
        payload = request.get_json(force=True, silent=True) or {}
        now = now_utc()
        allowed = {"ffmpeg_path", "ffprobe_path", "webhook_export_url", "api_rps"}
        for k, v in payload.items():
            if k in allowed:
                storage.set_setting(conn, k, (str(v) if v is not None else "").strip(), now)
        _audit("settings_update", {"keys": list(payload.keys())}, project_code=None)
        return jsonify({"ok": True})

    # ----------------- API: projects + assets -----------------
    @app.get("/api/projects")
    def api_projects_list():
        return jsonify(storage.list_projects(conn, limit=500))

    @app.get("/api/projects/<code>")
    def api_project_get(code):
        p = storage.get_project(conn, code)
        if not p:
            return jsonify({"error": "not_found"}), 404
        assets = storage.list_assets(conn, code, limit=500)
        exports = storage.list_exports(conn, code, limit=200)
        return jsonify({"project": p, "assets": assets, "exports": exports})

    @app.post("/api/orders")
    def api_orders():
        payload = request.get_json(force=True, silent=True) or {}
        action = payload.get("action")
        if action != "create_project":
            return jsonify({"error": "unsupported_action", "supported": ["create_project"]}), 400
        project = payload.get("project") or {}
        if not isinstance(project, dict):
            return jsonify({"error": "project_must_be_object"}), 400
        spec = parse_project_spec(project)
        code = (_slug(spec.title)[:24]).upper()
        now = now_utc()
        storage.upsert_project(conn, {
            "project_code": code,
            "title": spec.title,
            "studio": spec.studio,
            "audio_type": spec.audio_type,
            "genre": spec.genre,
            "bpm": spec.bpm,
            "musical_key": spec.musical_key,
            "mood": spec.mood,
            "reference_notes": spec.references,
            "lyrics_theme": spec.lyrics_theme,
            "notes": spec.notes,
            "status": "Draft",
            "created_at": now,
            "updated_at": now,
        })
        _audit("order_create_project", {"project": spec.title}, project_code=code)
        return jsonify({"ok": True, "project_code": code})

    # ----------------- API: generation workflow -----------------
    @app.post("/api/generate/pilot")
    def api_generate_pilot():
        payload = request.get_json(force=True, silent=True) or {}
        code = payload.get("project_code")
        if not code:
            return jsonify({"error": "project_code_required"}), 400
        p = storage.get_project(conn, code)
        if not p:
            return jsonify({"error": "not_found"}), 404

        spec = parse_project_spec(p)
        pack = build_pilot_pack(spec, llm=llm)
        storage.add_asset(conn, code, "pilot_pack", json.dumps(pack, ensure_ascii=False, indent=2), now_utc())
        _audit("pilot_generated", {"llm": llm.status()}, project_code=code)
        return jsonify({"ok": True, "project_code": code, "pilot_pack": pack})


    @app.post("/api/generate/sonic_brand")
    def api_generate_sonic_brand():
        payload = request.get_json(force=True, silent=True) or {}
        code = payload.get("project_code")
        if not code:
            return jsonify({"error": "project_code_required"}), 400
        p = storage.get_project(conn, code)
        if not p:
            return jsonify({"error": "not_found"}), 404
        spec = parse_project_spec(p)
        pack = build_sonic_brand_pack(spec, llm=llm)
        storage.add_asset(conn, code, "sonic_brand_pack", json.dumps(pack, ensure_ascii=False, indent=2), now_utc())
        _audit("sonic_brand_generated", {"llm": llm.status()}, project_code=code)
        return jsonify({"ok": True, "project_code": code, "sonic_brand_pack": pack})

    @app.post("/api/generate/podcast_pack")
    def api_generate_podcast_pack():
        payload = request.get_json(force=True, silent=True) or {}
        code = payload.get("project_code")
        if not code:
            return jsonify({"error": "project_code_required"}), 400
        p = storage.get_project(conn, code)
        if not p:
            return jsonify({"error": "not_found"}), 404
        spec = parse_project_spec(p)
        pack = build_podcast_pack(spec, llm=llm)
        storage.add_asset(conn, code, "podcast_pack", json.dumps(pack, ensure_ascii=False, indent=2), now_utc())
        _audit("podcast_pack_generated", {"llm": llm.status()}, project_code=code)
        return jsonify({"ok": True, "project_code": code, "podcast_pack": pack})

    @app.post("/api/generate/voice_pack")
    def api_generate_voice_pack():
        payload = request.get_json(force=True, silent=True) or {}
        code = payload.get("project_code")
        if not code:
            return jsonify({"error": "project_code_required"}), 400
        p = storage.get_project(conn, code)
        if not p:
            return jsonify({"error": "not_found"}), 404
        spec = parse_project_spec(p)
        pack = build_voice_pack(spec, llm=llm)
        storage.add_asset(conn, code, "voice_pack", json.dumps(pack, ensure_ascii=False, indent=2), now_utc())
        _audit("voice_pack_generated", {"llm": llm.status()}, project_code=code)
        return jsonify({"ok": True, "project_code": code, "voice_pack": pack})

    @app.post("/api/generate/score_cue_pack")
    def api_generate_score_cue_pack():
        payload = request.get_json(force=True, silent=True) or {}
        code = payload.get("project_code")
        if not code:
            return jsonify({"error": "project_code_required"}), 400
        p = storage.get_project(conn, code)
        if not p:
            return jsonify({"error": "not_found"}), 404
        spec = parse_project_spec(p)
        pack = build_score_cue_pack(spec, llm=llm)
        storage.add_asset(conn, code, "score_cue_pack", json.dumps(pack, ensure_ascii=False, indent=2), now_utc())
        _audit("score_cue_pack_generated", {"llm": llm.status()}, project_code=code)
        return jsonify({"ok": True, "project_code": code, "score_cue_pack": pack})

    @app.post("/api/generate/sfx_pack")
    def api_generate_sfx_pack():
        payload = request.get_json(force=True, silent=True) or {}
        code = payload.get("project_code")
        if not code:
            return jsonify({"error": "project_code_required"}), 400
        p = storage.get_project(conn, code)
        if not p:
            return jsonify({"error": "not_found"}), 404
        spec = parse_project_spec(p)
        pack = build_sfx_pack(spec, llm=llm)
        storage.add_asset(conn, code, "sfx_pack", json.dumps(pack, ensure_ascii=False, indent=2), now_utc())
        _audit("sfx_pack_generated", {"llm": llm.status()}, project_code=code)
        return jsonify({"ok": True, "project_code": code, "sfx_pack": pack})

    @app.post("/api/agents/clarify")
    def api_agents_clarify():
        payload = request.get_json(force=True, silent=True) or {}
        code = payload.get("project_code")
        p = storage.get_project(conn, code) if code else None
        spec_obj = p or payload.get("spec") or {}
        out = llm.clarify(spec_obj if isinstance(spec_obj, dict) else {"raw": str(spec_obj)})
        if code:
            storage.add_asset(conn, code, "clarify_questions", json.dumps(out, ensure_ascii=False, indent=2), now_utc())
            _audit("agent_clarify", {"ok": True}, project_code=code)
        return jsonify(out)

    @app.post("/api/agents/director")
    def api_agents_director():
        payload = request.get_json(force=True, silent=True) or {}
        code = payload.get("project_code")
        if not code:
            return jsonify({"error": "project_code_required"}), 400
        p = storage.get_project(conn, code)
        if not p:
            return jsonify({"error": "not_found"}), 404
        pack = _get_latest_pack(code)
        out = llm.director_notes(p, pack)
        storage.add_asset(conn, code, "producer_notes", json.dumps(out, ensure_ascii=False, indent=2), now_utc())
        _audit("agent_producer_notes", {"ok": True}, project_code=code)
        return jsonify(out)

    @app.post("/api/critic/run")
    def api_critic_run():
        payload = request.get_json(force=True, silent=True) or {}
        code = payload.get("project_code")
        if not code:
            return jsonify({"error": "project_code_required"}), 400
        pack = _get_latest_pack(code)
        if not pack:
            return jsonify({"error": "no_pilot_pack"}), 400
        out = llm.critique(pack) or {"llm_used": False, "note": "LLM disabled; no critique."}
        storage.add_asset(conn, code, "critic_report", json.dumps(out, ensure_ascii=False, indent=2), now_utc())
        _audit("critic_run", {"ok": True}, project_code=code)
        return jsonify(out)

    @app.post("/api/timeline/update")
    def api_timeline_update():
        payload = request.get_json(force=True, silent=True) or {}
        code = payload.get("project_code")
        timeline = payload.get("timeline")
        if not code or timeline is None:
            return jsonify({"error": "project_code_and_timeline_required"}), 400
        pack = _get_latest_pack(code)
        if not pack:
            return jsonify({"error": "no_pilot_pack"}), 400
        pack["timeline"] = timeline
        storage.add_asset(conn, code, "pilot_pack", json.dumps(pack, ensure_ascii=False, indent=2), now_utc())
        _audit("timeline_update", {"items": len(timeline) if isinstance(timeline, list) else None}, project_code=code)
        return jsonify({"ok": True, "project_code": code})

    @app.post("/api/timeline/regenerate")
    def api_timeline_regenerate():
        payload = request.get_json(force=True, silent=True) or {}
        code = payload.get("project_code")
        clip_id = payload.get("clip_id")
        instruction = (payload.get("instruction") or "").strip()
        if not code or not clip_id or not instruction:
            return jsonify({"error": "project_code_clip_id_instruction_required"}), 400
        pack = _get_latest_pack(code)
        if not pack:
            return jsonify({"error": "no_pilot_pack"}), 400
        # LLM attempt
        upd = llm.regenerate_clip(pack, clip_id, instruction) or {}
        updated_clip = upd.get("updated_clip") if isinstance(upd, dict) else None
        if not updated_clip:
            # offline: mutate notes only
            for c in (pack.get("timeline") or []):
                if c.get("clip_id") == clip_id:
                    c["notes"] = (c.get("notes") or "") + f" | REGEN: {instruction}"
                    updated_clip = c
                    break
        if updated_clip:
            # apply updated clip
            tl = pack.get("timeline") or []
            for i, c in enumerate(tl):
                if c.get("clip_id") == clip_id:
                    tl[i] = updated_clip
                    break
            pack["timeline"] = tl
            storage.add_asset(conn, code, "pilot_pack", json.dumps(pack, ensure_ascii=False, indent=2), now_utc())
            storage.add_asset(conn, code, "regen_event", json.dumps({"clip_id": clip_id, "instruction": instruction, "llm": upd}, ensure_ascii=False, indent=2), now_utc())
            _audit("timeline_regenerate", {"clip_id": clip_id}, project_code=code)
        return jsonify({"ok": True, "clip_id": clip_id, "updated_clip": updated_clip or {}})


    # ----------------- ZoomBot + Avatar stubs (offline-first) -----------------
    @app.post("/api/zoombot/ingest")
    def api_zoombot_ingest():
        payload = request.get_json(force=True, silent=True) or {}
        code = payload.get("project_code")
        transcript = (payload.get("transcript") or "").strip()
        if not transcript:
            return jsonify({"error": "transcript_required"}), 400

        # Offline-first summary
        summary = {
            "mode": "offline" if not llm.status().get("openai_enabled") else "openai",
            "summary": transcript[:600] + ("…" if len(transcript) > 600 else ""),
            "action_items": ["Review transcript", "Confirm deliverables", "Approve next steps"],
            "show_notes": "(Set OPENAI_API_KEY for full summarisation.)",
        }

        # If LLM enabled, ask for structured show notes.
        if llm.status().get("openai_enabled"):
            try:
                summary = llm._call_json(
                    "Summarise this meeting transcript for an audio production engine. Return JSON with summary, action_items, risks, decisions, show_notes.",
                    {"transcript": transcript},
                    max_output_tokens=1200,
                )
                summary.setdefault("mode", "openai")
            except Exception:
                pass

        if code:
            storage.add_asset(conn, code, "zoombot_ingest", json.dumps(summary, ensure_ascii=False, indent=2), now_utc())
            _audit("zoombot_ingest", {"chars": len(transcript)}, project_code=code)
        return jsonify({"ok": True, **summary})

    @app.post("/api/avatar/profile")
    def api_avatar_profile():
        payload = request.get_json(force=True, silent=True) or {}
        code = payload.get("project_code")
        persona = (payload.get("persona") or "Producer").strip()[:80]
        context = (payload.get("context") or "").strip()

        prof = {
            "mode": "offline" if not llm.status().get("openai_enabled") else "openai",
            "persona": persona,
            "voice_tone": "confident, concise, production-first",
            "catchphrases": ["Keep the low-end clean.", "Hook early, earn the drop.", "No clipping. Ever."],
            "do": ["give clear steps", "call out risks", "keep it publishable"],
            "dont": ["copy melodies", "overcomplicate", "ship without QC"],
        }

        if llm.status().get("openai_enabled"):
            try:
                prof = llm._call_json(
                    "Create an internal avatar profile for an audio engine persona. Return JSON with voice_tone, do, dont, catchphrases, checklist.",
                    {"persona": persona, "context": context},
                    max_output_tokens=900,
                )
                prof.setdefault("persona", persona)
                prof.setdefault("mode", "openai")
            except Exception:
                pass

        if code:
            storage.add_asset(conn, code, "avatar_profile", json.dumps(prof, ensure_ascii=False, indent=2), now_utc())
            _audit("avatar_profile", {"persona": persona}, project_code=code)
        return jsonify({"ok": True, **prof})

    @app.post("/api/approve")
    def api_approve():
        payload = request.get_json(force=True, silent=True) or {}
        code = payload.get("project_code")
        if not code:
            return jsonify({"error": "project_code_required"}), 400
        p = storage.get_project(conn, code)
        if not p:
            return jsonify({"error": "not_found"}), 404
        p["status"] = "Approved"
        p["updated_at"] = now_utc()
        storage.upsert_project(conn, p)
        storage.add_asset(conn, code, "approval", json.dumps({"approved_at": now_utc()}, indent=2), now_utc())
        _audit("approve", {"ok": True}, project_code=code)
        return jsonify({"ok": True, "project_code": code, "status": "Approved"})

    # ----------------- API: uploads (audio) -----------------
    @app.post("/api/upload/audio")
    def api_upload_audio():
        if "file" not in request.files:
            return jsonify({"error": "file_required"}), 400
        f = request.files["file"]
        if not f.filename:
            return jsonify({"error": "filename_required"}), 400
        code = (request.form.get("project_code") or "").strip().upper() or None
        filename = safe_filename(f.filename)
        data = f.read()
        out_path = (dirs["uploads"] / filename)
        out_path.write_bytes(data)
        analysis = analyze_audio_file(out_path)
        if code:
            storage.add_asset(conn, code, "uploaded_audio", json.dumps(analysis, indent=2), now_utc())
            _audit("audio_upload", {"filename": filename}, project_code=code)
        return jsonify({"ok": True, "analysis": analysis})

    

    # ----------------- API: audio utility (P11→P16) -----------------
    @app.post("/api/audio/trim")
    def api_audio_trim():
        """Server-side WAV trim.

        Multipart form fields:
        - file: WAV
        - start_sec, end_sec
        - project_code (optional)
        """
        if "file" not in request.files:
            return jsonify({"error": "file_required"}), 400
        f = request.files["file"]
        if not f.filename:
            return jsonify({"error": "filename_required"}), 400
        code = (request.form.get("project_code") or "").strip().upper() or None
        start_sec = request.form.get("start_sec", "0")
        end_sec = request.form.get("end_sec", "0")
        try:
            start = float(start_sec)
            end = float(end_sec)
        except Exception:
            return jsonify({"error": "start_end_must_be_numbers"}), 400

        filename = safe_filename(f.filename)
        data = f.read()
        in_path = dirs["uploads"] / filename
        in_path.write_bytes(data)
        converted = False
        try:
            wav_path = ensure_audio_wav(in_path, dirs["tmp"])
            converted = (wav_path != in_path)
        except Exception as e:
            return jsonify({"error": str(e), "hint": "Install ffmpeg or upload WAV."}), 400

        # If end is 0/negative, treat as full duration.
        if end <= 0:
            try:
                a = analyze_audio_file(wav_path)
                if a.get("duration_sec"):
                    end = float(a["duration_sec"])
            except Exception:
                pass

        out_name = safe_filename(f"TRIM_{code or 'AUDIO'}_{os.urandom(4).hex()}.wav")
        out_path = dirs["exports"] / out_name
        try:
            out_analysis = wav_trim(wav_path, start, end, out_path)
        except Exception as e:
            return jsonify({"error": str(e)}), 400

        if code:
            storage.add_asset(conn, code, "audio_trim", json.dumps({"source": filename, "start_sec": start, "end_sec": end, "output": out_name, "analysis": out_analysis}, indent=2), now_utc())
            _audit("audio_trim", {"output": out_name}, project_code=code)

        return jsonify({
            "ok": True,
            "output_filename": out_name,
            "download_url": f"/exports/{out_name}",
            "media_url": f"/media/exports/{out_name}",
            "analysis": out_analysis,
            "converted": converted,
        })


    @app.post("/api/audio/segment_pack")
    def api_audio_segment_pack():
        """Create a ZIP of WAV segments from markers.

        Multipart form fields:
        - file: WAV
        - markers_json: JSON list of seconds (optional)
        - project_code (optional)
        """
        if "file" not in request.files:
            return jsonify({"error": "file_required"}), 400
        f = request.files["file"]
        if not f.filename:
            return jsonify({"error": "filename_required"}), 400
        code = (request.form.get("project_code") or "").strip().upper() or None
        markers_raw = request.form.get("markers_json") or request.form.get("markers") or "[]"
        try:
            markers = json.loads(markers_raw) if markers_raw else []
        except Exception:
            markers = []
        if not isinstance(markers, list):
            markers = []

        filename = safe_filename(f.filename)
        data = f.read()
        in_path = dirs["uploads"] / filename
        in_path.write_bytes(data)
        converted = False
        try:
            wav_path = ensure_audio_wav(in_path, dirs["tmp"])
            converted = (wav_path != in_path)
        except Exception as e:
            return jsonify({"error": str(e), "hint": "Install ffmpeg or upload WAV."}), 400

        base_name = safe_filename(f"{code or 'AUDIO'}_{Path(filename).stem}")
        seg_dir = dirs["outputs"] / safe_filename(f"SEG_{base_name}_{os.urandom(4).hex()}")
        try:
            segments = wav_split_by_markers(wav_path, markers, seg_dir, base_name)
        except Exception as e:
            return jsonify({"error": str(e)}), 400

        if not segments:
            return jsonify({"error": "no_segments_generated"}), 400

        stems_csv = stems_index_csv_from_segments(segments)

        issue = make_issue_ref("AUDIOSEG", (code or "AUDIO"), int(datetime.now().timestamp()) % 10000)
        zip_name = safe_filename(f"{issue}_segments.zip")
        zip_path = dirs["exports"] / zip_name

        import io, zipfile
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as z:
            z.writestr("source_analysis.json", json.dumps(analyze_audio_file(wav_path), indent=2))
            z.writestr("markers.json", json.dumps(markers, indent=2))
            z.writestr("segments_manifest.json", json.dumps(segments, indent=2))
            z.writestr("stems_index.csv", stems_csv)
            for seg in segments:
                p = seg_dir / seg["filename"]
                if p.exists():
                    z.write(str(p), arcname=f"segments/{p.name}")

        blob = buf.getvalue()
        zip_path.write_bytes(blob)

        if code:
            storage.add_asset(conn, code, "audio_segment_pack", json.dumps({"source": filename, "zip": zip_name, "segments": segments}, indent=2), now_utc())
            _audit("audio_segment_pack", {"zip": zip_name, "count": len(segments)}, project_code=code)

        return jsonify({
            "ok": True,
            "zip_filename": zip_name,
            "download_url": f"/exports/{zip_name}",
            "segment_count": len(segments),
            "converted": converted,
        })

    @app.get("/api/audio/qc/targets")
    def api_audio_qc_targets():
        return jsonify({"ok": True, "targets": default_loudness_targets()})

    @app.post("/api/audio/qc")
    def api_audio_qc():
        """Loudness QC.

        Multipart form fields:
        - file: audio
        - target_profile: e.g. streaming_general / podcast_stereo
        - project_code (optional)
        """
        if "file" not in request.files:
            return jsonify({"error": "file_required"}), 400
        f = request.files["file"]
        if not f.filename:
            return jsonify({"error": "filename_required"}), 400
        code = (request.form.get("project_code") or "").strip().upper() or None
        target_profile = (request.form.get("target_profile") or "streaming_general").strip()

        filename = safe_filename(f.filename)
        in_path = dirs["uploads"] / filename
        in_path.write_bytes(f.read())
        try:
            wav_path = ensure_audio_wav(in_path, dirs["tmp"])
        except Exception as e:
            return jsonify({"error": str(e), "hint": "Install ffmpeg for non-WAV QC."}), 400

        rep = loudness_qc_report(wav_path, target_profile=target_profile)
        if code:
            storage.add_asset(conn, code, "loudness_qc", json.dumps(rep, ensure_ascii=False, indent=2), now_utc())
            _audit("loudness_qc", {"target": target_profile, "ok": rep.get("ok")}, project_code=code)
        return jsonify(rep)

    # ----------------- API: mix board (P21 step 1) -----------------
    @app.get("/api/mix/buses/<project_id>")
    def api_mix_buses(project_id):
        code = (project_id or "").strip().upper()
        if not code:
            return jsonify({"error": "project_id_required"}), 400
        p = storage.get_project(conn, code)
        if not p:
            return jsonify({"error": "not_found"}), 404
        return jsonify({"ok": True, "project_code": code, "buses": storage.list_mix_buses(conn, code)})

    @app.post("/api/mix/bus")
    def api_mix_bus_upsert():
        payload = request.get_json(force=True, silent=True) or {}
        code = (payload.get("project_id") or payload.get("project_code") or "").strip().upper()
        name = (payload.get("name") or "main").strip()
        tracks = payload.get("tracks") or []
        if not code:
            return jsonify({"error": "project_id_required"}), 400
        if not name:
            return jsonify({"error": "name_required"}), 400
        if not isinstance(tracks, list):
            return jsonify({"error": "tracks_must_be_array"}), 400
        cleaned = []
        for t in tracks:
            if not isinstance(t, dict) or t.get("asset_id") is None:
                continue
            try:
                aid = int(t.get("asset_id"))
                gain = float(t.get("gain_db") or 0.0)
                pan = float(t.get("pan") or 0.0)
            except Exception:
                continue
            cleaned.append({"asset_id": aid, "gain_db": gain, "pan": max(-1.0, min(1.0, pan))})
        bus = storage.upsert_mix_bus(conn, code, name, cleaned, now_utc())
        _audit("mix_bus_upsert", {"bus_id": bus.get("id"), "name": name, "tracks": len(cleaned)}, project_code=code)
        return jsonify({"ok": True, "bus": bus})

    @app.delete("/api/mix/bus/<bus_id>")
    def api_mix_bus_delete(bus_id):
        try:
            bid = int(bus_id)
        except Exception:
            return jsonify({"error": "invalid_bus_id"}), 400
        ok = storage.delete_mix_bus(conn, bid)
        if not ok:
            return jsonify({"error": "not_found"}), 404
        return jsonify({"ok": True})

    @app.post("/api/mix/render")
    def api_mix_render():
        payload = request.get_json(force=True, silent=True) or {}
        code = (payload.get("project_id") or payload.get("project_code") or "").strip().upper()
        name = (payload.get("name") or "").strip()
        if not code or not name:
            return jsonify({"error": "project_id_and_name_required"}), 400
        buses = storage.list_mix_buses(conn, code)
        bus = next((b for b in buses if (b.get("name") or "") == name), None)
        if not bus:
            return jsonify({"error": "bus_not_found"}), 404
        try:
            mix = storage.mix_audio(conn, dirs, code, name, bus.get("tracks") or [], now_utc())
        except Exception as e:
            msg = str(e)
            hint = None
            if "non_wav_track" in msg:
                hint = "Only WAV tracks are mixable right now. Convert first via Trim utility."
            return jsonify({"error": msg, "hint": hint}), 400
        _audit("mix_render", {"name": name, "filename": mix.get("filename")}, project_code=code)
        return jsonify({"ok": True, "mix": mix, "download_url": f"/exports/{mix.get('filename')}"})


    # ----------------- API: timeline editor (P21 step 2) -----------------
    @app.post("/api/timeline")
    def api_timeline_upsert():
        payload = request.get_json(force=True, silent=True) or {}
        code = (payload.get("project_id") or payload.get("project_code") or "").strip().upper()
        name = (payload.get("name") or "main").strip()
        tracks = payload.get("tracks") or []
        if not code:
            return jsonify({"error": "project_id_required"}), 400
        if not name:
            return jsonify({"error": "name_required"}), 400
        if not isinstance(tracks, list):
            return jsonify({"error": "tracks_must_be_array"}), 400
        cleaned = []
        for t in tracks:
            if not isinstance(t, dict) or t.get("asset_id") is None:
                continue
            try:
                cleaned.append({
                    "asset_id": int(t.get("asset_id")),
                    "start_ms": max(0, int(float(t.get("start_ms") or 0))),
                    "duration_ms": max(0, int(float(t.get("duration_ms") or 0))),
                    "bus_name": (t.get("bus_name") or "").strip() or None,
                })
            except Exception:
                continue
        tl = storage.upsert_timeline(conn, code, name, cleaned, now_utc())
        _audit("timeline_upsert", {"timeline_id": tl.get("id"), "name": name, "tracks": len(cleaned)}, project_code=code)
        return jsonify({"ok": True, "timeline": tl})

    @app.get("/api/timeline/<project_id>")
    def api_timeline_list(project_id):
        code = (project_id or "").strip().upper()
        if not code:
            return jsonify({"error": "project_id_required"}), 400
        return jsonify({"ok": True, "project_id": code, "timelines": storage.list_timelines(conn, code)})

    @app.delete("/api/timeline/<timeline_id>")
    def api_timeline_delete(timeline_id):
        try:
            tid = int(timeline_id)
        except Exception:
            return jsonify({"error": "invalid_timeline_id"}), 400
        ok = storage.delete_timeline(conn, tid)
        if not ok:
            return jsonify({"error": "not_found"}), 404
        return jsonify({"ok": True})

    @app.post("/api/timeline/render")
    def api_timeline_render():
        payload = request.get_json(force=True, silent=True) or {}
        code = (payload.get("project_id") or payload.get("project_code") or "").strip().upper()
        if not code:
            return jsonify({"error": "project_id_required"}), 400

        timeline = None
        if payload.get("timeline_id") is not None:
            try:
                timeline = storage.get_timeline(conn, int(payload.get("timeline_id")))
            except Exception:
                timeline = None
        if timeline is None and payload.get("name"):
            name = (payload.get("name") or "").strip()
            timeline = next((x for x in storage.list_timelines(conn, code) if (x.get("name") or "") == name), None)
        if timeline is None:
            return jsonify({"error": "timeline_not_found"}), 404

        buses = {b.get("name"): b for b in storage.list_mix_buses(conn, code)}
        merged_tracks = []
        for t in (timeline.get("tracks") or []):
            if t.get("asset_id") is None:
                continue
            bus_name = (t.get("bus_name") or "").strip()
            gain_db = 0.0
            pan = 0.0
            if bus_name and buses.get(bus_name):
                for bt in (buses[bus_name].get("tracks") or []):
                    if int(bt.get("asset_id") or 0) == int(t.get("asset_id")):
                        gain_db = float(bt.get("gain_db") or 0.0)
                        pan = float(bt.get("pan") or 0.0)
                        break
            merged_tracks.append({
                "asset_id": int(t.get("asset_id")),
                "start_ms": max(0, int(float(t.get("start_ms") or 0))),
                "duration_ms": max(0, int(float(t.get("duration_ms") or 0))),
                "gain_db": gain_db,
                "pan": pan,
            })

        if not merged_tracks:
            return jsonify({"error": "timeline_has_no_tracks"}), 400

        try:
            mix = storage.mix_audio(conn, dirs, code, f"timeline_{timeline.get('name')}", merged_tracks, now_utc(), output_kind="timeline_mix")
        except Exception as e:
            msg = str(e)
            hint = "Only WAV tracks are mixable right now. Convert first via Trim utility." if "non_wav_track" in msg else None
            return jsonify({"error": msg, "hint": hint}), 400

        _audit("timeline_render", {"timeline_id": timeline.get("id"), "filename": mix.get("filename")}, project_code=code)
        return jsonify({"ok": True, "timeline": timeline, "mix": mix, "download_url": f"/exports/{mix.get('filename')}"})


# ----------------- Export / import -----------------
    def build_export_zip(issue_ref: str, project_row: dict, pilot_pack: dict, platform_files: dict, export_meta: dict) -> bytes:
        import io, zipfile
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as z:
            z.writestr("issue_ref.txt", issue_ref)
            z.writestr("project.json", json.dumps(project_row, indent=2, ensure_ascii=False))
            z.writestr("pilot_pack.json", json.dumps(pilot_pack, indent=2, ensure_ascii=False))
            # Include other pack types if generated
            extra_kinds = [
                ("sonic_brand_pack", "sonic_brand_pack.json"),
                ("podcast_pack", "podcast_pack.json"),
                ("voice_pack", "voice_pack.json"),
                ("score_cue_pack", "score_cue_pack.json"),
                ("sfx_pack", "sfx_pack.json"),
            ]
            for kind, fname in extra_kinds:
                a = storage.list_assets(conn, project_row["project_code"], kind=kind, limit=1)
                if a and a[0].get("payload_json"):
                    z.writestr(fname, a[0]["payload_json"])

            # Loudness QC report (if run)
            qc = storage.list_assets(conn, project_row["project_code"], kind="loudness_qc", limit=1)
            if qc and qc[0].get("payload_json"):
                z.writestr("qc/loudness_qc_report.json", qc[0]["payload_json"])
            z.writestr("export_meta.json", json.dumps(export_meta, indent=2))
            z.writestr("WORKFLOW.md", "# CCE Audio Workflow (Internal)\n\n1) Studio Hub submits order\n2) CCE Audio generates pack\n3) Critique + founder signoff\n4) Export pack\n5) Studio Hub schedules/publishes\n")
            for plat, files in platform_files.items():
                for rel, content in files.items():
                    z.writestr(f"platforms/{plat}/{rel}", content)
            # include latest producer notes / critic if present
            for kind, name in (("producer_notes", "producer_notes.json"), ("critic_report", "critic_report.json")):
                a = storage.list_assets(conn, project_row["project_code"], kind=kind, limit=1)
                if a:
                    z.writestr(name, a[0]["payload_json"] or "")

            manifest = {}
            for info in z.infolist():
                if info.is_dir():
                    continue
                data = z.read(info.filename)
                manifest[info.filename] = {"sha256": sha256_bytes(data), "bytes": len(data)}

            # Include a self-entry for tooling expectations (non-recursive checksum).
            manifest["manifest.json"] = {"sha256": "", "bytes": 0}
            mb = json.dumps(manifest, indent=2).encode("utf-8")
            manifest["manifest.json"] = {"sha256": sha256_bytes(mb), "bytes": len(mb)}
            z.writestr("manifest.json", json.dumps(manifest, indent=2))
        return buf.getvalue()

    @app.post("/api/export")
    def api_export():
        payload = request.get_json(force=True, silent=True) or {}
        code = payload.get("project_code")
        force = bool(payload.get("force"))
        if not code:
            return jsonify({"error": "project_code_required"}), 400
        p = storage.get_project(conn, code)
        if not p:
            return jsonify({"error": "not_found"}), 404
        if (p.get("status") != "Approved") and not force:
            return jsonify({"error": "not_approved", "hint": "Run critique + approve before export (or pass force=true for internal draft export)"}), 400
        pilot_pack = _get_latest_pack(code)
        if not pilot_pack:
            return jsonify({"error": "no_pilot_pack", "hint": "Run Generate Pilot first"}), 400

        seq = len(storage.list_exports(conn, code)) + 1
        issue_ref = make_issue_ref("AUDIO", code, seq)
        platform_files = render_platform_packs(code, pilot_pack)

        export_meta = {
            "issue_ref": issue_ref,
            "project_code": code,
            "created_at": now_utc(),
            "approved": (p.get("status") == "Approved"),
            "force": force,
            "engine": {
                "name": meta.get("name", "CCE Audio Studio"),
                "key": meta.get("key", "cce.audio.studio.mvp5"),
                "version": meta.get("version", "P9"),
            },
        }

        blob = build_export_zip(issue_ref, p, pilot_pack, platform_files, export_meta)
        filename = f"{issue_ref}.zip"
        out_path = dirs["exports"] / filename
        out_path.write_bytes(blob)
        storage.add_export(conn, code, issue_ref, filename, sha256_bytes(blob), len(blob), now_utc())
        storage.add_asset(conn, code, "export_event", json.dumps(export_meta, indent=2), now_utc())
        _audit("export", {"filename": filename}, project_code=code)

        # Best-effort webhook for hubs.
        try:
            _fire_export_webhook({
                "event": "export_complete",
                "issue_ref": issue_ref,
                "project_code": code,
                "filename": filename,
                "download_url": url_for("download_export", filename=filename, _external=True),
                "created_at": export_meta.get("created_at"),
            })
        except Exception:
            pass
        return jsonify({"ok": True, "issue_ref": issue_ref, "filename": filename})

    @app.get("/exports/<path:filename>")
    def download_export(filename):
        return send_from_directory(str(dirs["exports"]), filename, as_attachment=True)


    @app.get("/media/exports/<path:filename>")
    def media_export(filename):
        # same file as /exports but without forced download
        return send_from_directory(str(dirs["exports"]), filename, as_attachment=False)

    @app.post("/api/import")
    def api_import():
        note = request.form.get("note")
        if "file" not in request.files:
            return jsonify({"error": "file_required"}), 400
        f = request.files["file"]
        if not f.filename:
            return jsonify({"error": "filename_required"}), 400
        data = f.read()
        sha = sha256_bytes(data)
        filename = safe_filename(f.filename)
        out_path = dirs["imports"] / filename
        out_path.write_bytes(data)
        storage.add_import(conn, filename, sha, len(data), note or "", now_utc())
        _audit("import", {"filename": filename}, project_code=None)
        return jsonify({"ok": True, "filename": filename, "sha256": sha})



    @app.get("/imports/<path:filename>")
    def download_import(filename):
        return send_from_directory(str(dirs["imports"]), filename, as_attachment=True)

    # ---------------------------
    # KR_STD Hub Handshake v1
    # ---------------------------
    @app.post("/api/hub/order")
    def hub_order():
        payload = request.get_json(force=True, silent=True) or {}
        kind = payload.get("kind") or payload.get("job_type") or "audio_job"
        order_id = payload.get("order_id") or f"AUD_{os.urandom(5).hex()}"
        storage.create_job(conn, order_id, kind, json.dumps(payload), "queued", now_utc())
        _audit("hub_order", {"order_id": order_id, "kind": kind}, project_code=None)
        return jsonify({"ok": True, "order_id": order_id, "status": "queued"})

    @app.get("/api/hub/status/<order_id>")
    def hub_status(order_id):
        j = storage.get_job(conn, order_id)
        if not j:
            return jsonify({"ok": False, "error": "not_found"}), 404
        return jsonify({"ok": True, "order_id": order_id, "status": j.get("status"), "updated_at": j.get("updated_at")})

    @app.get("/api/hub/result/<order_id>")
    def hub_result(order_id):
        j = storage.get_job(conn, order_id)
        if not j:
            return jsonify({"ok": False, "error": "not_found"}), 404
        res = {"ok": True, "order_id": order_id, "status": j.get("status"), "result": None}
        if j.get("result_json"):
            try:
                res["result"] = json.loads(j["result_json"])
            except Exception:
                res["result"] = {"raw": j.get("result_json")}
        fn = j.get("export_filename")
        if fn:
            res["export_ready"] = True
            res["download_url"] = url_for("download_export", filename=fn, _external=True)
        else:
            res["export_ready"] = False
        return jsonify(res)

    @app.post("/api/hub/receipt")
    def hub_receipt():
        payload = request.get_json(force=True, silent=True) or {}
        _audit("hub_receipt", payload, project_code=None)
        return jsonify({"ok": True})

    # ---------------------------
    # Jobs UI (visual queue)
    # ---------------------------
    @app.get("/jobs")
    def jobs_page():
        jobs = storage.list_jobs(conn, limit=250)
        return render_template("jobs.html", jobs=jobs)

    @app.post("/jobs/tick")
    def jobs_tick():
        try:
            worker.tick(max_jobs=3)
        except Exception:
            pass
        return redirect(url_for("jobs_page"))

    return app


if __name__ == "__main__":
    meta = load_meta()
    app = create_app()
    app.run(host="127.0.0.1", port=int(meta.get("port", 5204)), debug=False)

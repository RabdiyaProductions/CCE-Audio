import json
import math
import shutil
import sqlite3
import subprocess
import wave
from pathlib import Path
from typing import Any, Dict, List, Optional

SCHEMA_VERSION = 5


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def migrate(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()
    cur.execute("CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT)")
    cur.execute("SELECT v FROM meta WHERE k='schema_version'")
    row = cur.fetchone()
    v = int(row["v"]) if row else 0

    if v < 1:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS projects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_code TEXT UNIQUE,
                title TEXT,
                studio TEXT,
                audio_type TEXT,
                genre TEXT,
                bpm TEXT,
                musical_key TEXT,
                mood TEXT,
                reference_notes TEXT,
                lyrics_theme TEXT,
                notes TEXT,
                status TEXT,
                created_at TEXT,
                updated_at TEXT
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS project_assets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_code TEXT,
                kind TEXT,
                payload_json TEXT,
                created_at TEXT
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS export_registry (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_code TEXT,
                issue_ref TEXT,
                filename TEXT,
                sha256 TEXT,
                bytes INTEGER,
                created_at TEXT
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS import_registry (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filename TEXT,
                sha256 TEXT,
                bytes INTEGER,
                note TEXT,
                created_at TEXT
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                kind TEXT NOT NULL,
                project_code TEXT,
                payload_json TEXT NOT NULL
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                status TEXT NOT NULL,
                result_json TEXT,
                export_filename TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        cur.execute("INSERT OR REPLACE INTO meta (k,v) VALUES ('schema_version','1')")
        v = 1

    if v < 2:
        # Add simple indexes for scale
        cur.execute("CREATE INDEX IF NOT EXISTS idx_assets_project ON project_assets(project_code)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_exports_project ON export_registry(project_code)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_audit_project ON audit(project_code)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status)")
        cur.execute("INSERT OR REPLACE INTO meta (k,v) VALUES ('schema_version','2')")

    if v < 3:
        # Settings + smoke runs + blob scaffolding
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS settings (
                k TEXT PRIMARY KEY,
                v TEXT,
                updated_at TEXT
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS smoke_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                ok INTEGER NOT NULL,
                details_json TEXT NOT NULL
            )
            """
        )
        # Blob store is optional scaffolding: we don't force uploads through it yet.
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS blobs (
                blob_id TEXT PRIMARY KEY,
                bytes INTEGER,
                stored_path TEXT,
                created_at TEXT
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS project_blobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_code TEXT,
                blob_id TEXT,
                kind TEXT,
                filename TEXT,
                created_at TEXT
            )
            """
        )
        cur.execute("CREATE INDEX IF NOT EXISTS idx_settings_k ON settings(k)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_smoke_runs_ts ON smoke_runs(ts)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_project_blobs_project ON project_blobs(project_code)")
        cur.execute("INSERT OR REPLACE INTO meta (k,v) VALUES ('schema_version','3')")

    if v < 4:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS mix_buses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_code TEXT NOT NULL,
                name TEXT NOT NULL,
                tracks_json TEXT NOT NULL,
                created_at TEXT,
                updated_at TEXT,
                UNIQUE(project_code, name)
            )
            """
        )
        cur.execute("CREATE INDEX IF NOT EXISTS idx_mix_buses_project ON mix_buses(project_code)")
        cur.execute("INSERT OR REPLACE INTO meta (k,v) VALUES ('schema_version','4')")

    if v < 5:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS timelines (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id TEXT NOT NULL,
                name TEXT NOT NULL,
                tracks_json TEXT NOT NULL,
                created_at TEXT,
                updated_at TEXT,
                UNIQUE(project_id, name)
            )
            """
        )
        cur.execute("CREATE INDEX IF NOT EXISTS idx_timelines_project ON timelines(project_id)")
        cur.execute("INSERT OR REPLACE INTO meta (k,v) VALUES ('schema_version','5')")

    conn.commit()


# --- Settings ---

def get_setting(conn: sqlite3.Connection, key: str, default: Optional[str] = None) -> Optional[str]:
    cur = conn.cursor()
    r = cur.execute("SELECT v FROM settings WHERE k=?", (key,)).fetchone()
    return r["v"] if r else default


def set_setting(conn: sqlite3.Connection, key: str, value: str, updated_at: str) -> None:
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO settings(k,v,updated_at) VALUES (?,?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v, updated_at=excluded.updated_at",
        (key, value, updated_at),
    )
    conn.commit()


def list_settings(conn: sqlite3.Connection, limit: int = 200) -> List[Dict[str, Any]]:
    cur = conn.cursor()
    cur.execute("SELECT k,v,updated_at FROM settings ORDER BY k ASC LIMIT ?", (int(limit),))
    return [dict(r) for r in cur.fetchall()]


# --- Smoke runs ---

def add_smoke_run(conn: sqlite3.Connection, ts: str, ok: bool, details_json: str) -> None:
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO smoke_runs(ts,ok,details_json) VALUES (?,?,?)",
        (ts, 1 if ok else 0, details_json),
    )
    conn.commit()


def get_latest_smoke_run(conn: sqlite3.Connection) -> Optional[Dict[str, Any]]:
    cur = conn.cursor()
    r = cur.execute("SELECT * FROM smoke_runs ORDER BY id DESC LIMIT 1").fetchone()
    return dict(r) if r else None


# --- Projects ---

def upsert_project(conn: sqlite3.Connection, p: Dict[str, Any]) -> None:
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO projects (
            project_code,title,studio,audio_type,genre,bpm,musical_key,mood,reference_notes,lyrics_theme,notes,status,created_at,updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(project_code) DO UPDATE SET
            title=excluded.title,
            studio=excluded.studio,
            audio_type=excluded.audio_type,
            genre=excluded.genre,
            bpm=excluded.bpm,
            musical_key=excluded.musical_key,
            mood=excluded.mood,
            reference_notes=excluded.reference_notes,
            lyrics_theme=excluded.lyrics_theme,
            notes=excluded.notes,
            status=excluded.status,
            updated_at=excluded.updated_at
        """,
        (
            p["project_code"],
            p.get("title", ""),
            p.get("studio", ""),
            p.get("audio_type", ""),
            p.get("genre", ""),
            p.get("bpm", ""),
            p.get("musical_key", ""),
            p.get("mood", ""),
            p.get("reference_notes", ""),
            p.get("lyrics_theme", ""),
            p.get("notes", ""),
            p.get("status", "Draft"),
            p.get("created_at", ""),
            p.get("updated_at", ""),
        ),
    )
    conn.commit()


def list_projects(conn: sqlite3.Connection, limit: int = 500) -> List[Dict[str, Any]]:
    cur = conn.cursor()
    cur.execute("SELECT * FROM projects ORDER BY updated_at DESC LIMIT ?", (int(limit),))
    return [dict(r) for r in cur.fetchall()]


def get_project(conn: sqlite3.Connection, project_code: str) -> Optional[Dict[str, Any]]:
    cur = conn.cursor()
    cur.execute("SELECT * FROM projects WHERE project_code=?", (project_code,))
    r = cur.fetchone()
    return dict(r) if r else None


# --- Assets ---

def add_asset(conn: sqlite3.Connection, project_code: str, kind: str, payload_json: str, created_at: str) -> None:
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO project_assets(project_code,kind,payload_json,created_at) VALUES (?,?,?,?)",
        (project_code, kind, payload_json, created_at),
    )
    conn.commit()


def list_assets(conn: sqlite3.Connection, project_code: str, kind: Optional[str] = None, limit: int = 500) -> List[Dict[str, Any]]:
    cur = conn.cursor()
    if kind:
        cur.execute(
            "SELECT * FROM project_assets WHERE project_code=? AND kind=? ORDER BY id DESC LIMIT ?",
            (project_code, kind, int(limit)),
        )
    else:
        cur.execute(
            "SELECT * FROM project_assets WHERE project_code=? ORDER BY id DESC LIMIT ?",
            (project_code, int(limit)),
        )
    return [dict(r) for r in cur.fetchall()]


# --- Exports / Imports ---

def add_export(conn: sqlite3.Connection, project_code: str, issue_ref: str, filename: str, sha256: str, bytes_: int, created_at: str) -> None:
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO export_registry(project_code,issue_ref,filename,sha256,bytes,created_at) VALUES (?,?,?,?,?,?)",
        (project_code, issue_ref, filename, sha256, int(bytes_), created_at),
    )
    conn.commit()


def list_exports(conn: sqlite3.Connection, project_code: Optional[str] = None, limit: int = 500) -> List[Dict[str, Any]]:
    cur = conn.cursor()
    if project_code:
        cur.execute(
            "SELECT * FROM export_registry WHERE project_code=? ORDER BY id DESC LIMIT ?",
            (project_code, int(limit)),
        )
    else:
        cur.execute("SELECT * FROM export_registry ORDER BY id DESC LIMIT ?", (int(limit),))
    return [dict(r) for r in cur.fetchall()]


def add_import(conn: sqlite3.Connection, filename: str, sha256: str, bytes_: int, note: str, created_at: str) -> None:
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO import_registry(filename,sha256,bytes,note,created_at) VALUES (?,?,?,?,?)",
        (filename, sha256, int(bytes_), note or "", created_at),
    )
    conn.commit()


def list_imports(conn: sqlite3.Connection, limit: int = 200) -> List[Dict[str, Any]]:
    cur = conn.cursor()
    cur.execute("SELECT * FROM import_registry ORDER BY id DESC LIMIT ?", (int(limit),))
    return [dict(r) for r in cur.fetchall()]


# --- Audit ---

def audit(conn: sqlite3.Connection, kind: str, payload_json: str, ts: str, project_code: Optional[str] = None) -> None:
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO audit(ts,kind,project_code,payload_json) VALUES (?,?,?,?)",
        (ts, kind, project_code, payload_json),
    )
    conn.commit()


def list_audit(conn: sqlite3.Connection, project_code: Optional[str] = None, limit: int = 200) -> List[Dict[str, Any]]:
    cur = conn.cursor()
    if project_code:
        cur.execute(
            "SELECT * FROM audit WHERE project_code=? ORDER BY id DESC LIMIT ?",
            (project_code, int(limit)),
        )
    else:
        cur.execute("SELECT * FROM audit ORDER BY id DESC LIMIT ?", (int(limit),))
    return [dict(r) for r in cur.fetchall()]


# --- Jobs (Hub Orders) ---

def create_job(conn: sqlite3.Connection, job_id: str, kind: str, payload_json: str, status: str, created_at: str) -> None:
    cur = conn.cursor()
    cur.execute(
        "INSERT OR REPLACE INTO jobs(id,kind,payload_json,status,result_json,export_filename,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)",
        (job_id, kind, payload_json, status, None, None, created_at, created_at),
    )
    conn.commit()


def update_job(conn: sqlite3.Connection, job_id: str, status: Optional[str] = None, result_json: Optional[str] = None, export_filename: Optional[str] = None, updated_at: str = "") -> None:
    cur = conn.cursor()
    row = cur.execute("SELECT status,result_json,export_filename,created_at FROM jobs WHERE id=?", (job_id,)).fetchone()
    if not row:
        return
    new_status = status if status is not None else row["status"]
    new_result = result_json if result_json is not None else row["result_json"]
    new_export = export_filename if export_filename is not None else row["export_filename"]
    cur.execute(
        "UPDATE jobs SET status=?, result_json=?, export_filename=?, updated_at=? WHERE id=?",
        (new_status, new_result, new_export, updated_at, job_id),
    )
    conn.commit()


def get_job(conn: sqlite3.Connection, job_id: str) -> Optional[Dict[str, Any]]:
    cur = conn.cursor()
    r = cur.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    return dict(r) if r else None


def list_jobs(conn: sqlite3.Connection, status: Optional[str] = None, limit: int = 200) -> List[Dict[str, Any]]:
    cur = conn.cursor()
    if status:
        cur.execute("SELECT * FROM jobs WHERE status=? ORDER BY created_at DESC LIMIT ?", (status, int(limit)))
    else:
        cur.execute("SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (int(limit),))
    return [dict(r) for r in cur.fetchall()]


# --- Mix buses ---

def list_mix_buses(conn: sqlite3.Connection, project_code: str) -> List[Dict[str, Any]]:
    cur = conn.cursor()
    rows = cur.execute(
        "SELECT * FROM mix_buses WHERE project_code=? ORDER BY updated_at DESC, id DESC",
        (project_code,),
    ).fetchall()
    out: List[Dict[str, Any]] = []
    for r in rows:
        row = dict(r)
        try:
            row["tracks"] = json.loads(row.get("tracks_json") or "[]")
        except Exception:
            row["tracks"] = []
        out.append(row)
    return out


def upsert_mix_bus(conn: sqlite3.Connection, project_code: str, name: str, tracks: List[Dict[str, Any]], ts: str) -> Dict[str, Any]:
    cur = conn.cursor()
    tjson = json.dumps(tracks or [], ensure_ascii=False)
    cur.execute(
        """
        INSERT INTO mix_buses(project_code,name,tracks_json,created_at,updated_at)
        VALUES (?,?,?,?,?)
        ON CONFLICT(project_code, name) DO UPDATE SET
            tracks_json=excluded.tracks_json,
            updated_at=excluded.updated_at
        """,
        (project_code, name, tjson, ts, ts),
    )
    conn.commit()
    row = cur.execute("SELECT * FROM mix_buses WHERE project_code=? AND name=?", (project_code, name)).fetchone()
    out = dict(row)
    out["tracks"] = json.loads(out.get("tracks_json") or "[]")
    return out


def delete_mix_bus(conn: sqlite3.Connection, bus_id: int) -> bool:
    cur = conn.cursor()
    cur.execute("DELETE FROM mix_buses WHERE id=?", (int(bus_id),))
    conn.commit()
    return cur.rowcount > 0



# --- Timelines ---

def list_timelines(conn: sqlite3.Connection, project_id: str) -> List[Dict[str, Any]]:
    cur = conn.cursor()
    rows = cur.execute(
        "SELECT * FROM timelines WHERE project_id=? ORDER BY updated_at DESC, id DESC",
        (project_id,),
    ).fetchall()
    out: List[Dict[str, Any]] = []
    for r in rows:
        row = dict(r)
        try:
            row["tracks"] = json.loads(row.get("tracks_json") or "[]")
        except Exception:
            row["tracks"] = []
        out.append(row)
    return out


def upsert_timeline(conn: sqlite3.Connection, project_id: str, name: str, tracks: List[Dict[str, Any]], ts: str) -> Dict[str, Any]:
    cur = conn.cursor()
    tjson = json.dumps(tracks or [], ensure_ascii=False)
    cur.execute(
        """
        INSERT INTO timelines(project_id,name,tracks_json,created_at,updated_at)
        VALUES (?,?,?,?,?)
        ON CONFLICT(project_id, name) DO UPDATE SET
            tracks_json=excluded.tracks_json,
            updated_at=excluded.updated_at
        """,
        (project_id, name, tjson, ts, ts),
    )
    conn.commit()
    row = cur.execute("SELECT * FROM timelines WHERE project_id=? AND name=?", (project_id, name)).fetchone()
    out = dict(row)
    out["tracks"] = json.loads(out.get("tracks_json") or "[]")
    return out


def delete_timeline(conn: sqlite3.Connection, timeline_id: int) -> bool:
    cur = conn.cursor()
    cur.execute("DELETE FROM timelines WHERE id=?", (int(timeline_id),))
    conn.commit()
    return cur.rowcount > 0


def get_timeline(conn: sqlite3.Connection, timeline_id: int) -> Optional[Dict[str, Any]]:
    cur = conn.cursor()
    row = cur.execute("SELECT * FROM timelines WHERE id=?", (int(timeline_id),)).fetchone()
    if not row:
        return None
    out = dict(row)
    try:
        out["tracks"] = json.loads(out.get("tracks_json") or "[]")
    except Exception:
        out["tracks"] = []
    return out


def _resolve_asset_audio_path(conn: sqlite3.Connection, dirs: Dict[str, Path], project_code: str, asset_id: int) -> tuple[Path, dict]:
    assets = {a["id"]: a for a in list_assets(conn, project_code, limit=3000)}
    a = assets.get(int(asset_id))
    if not a:
        raise ValueError(f"asset_not_found:{asset_id}")
    meta = json.loads(a.get("payload_json") or "{}") if a.get("payload_json") else {}
    filename = (meta.get("filename") or meta.get("output") or "").strip()
    if not filename:
        raise ValueError(f"asset_missing_filename:{asset_id}")
    p = dirs["uploads"] / filename
    if not p.exists():
        p = dirs["exports"] / filename
    if not p.exists():
        raise ValueError(f"asset_file_not_found:{filename}")
    if p.suffix.lower() != ".wav":
        raise ValueError("non_wav_track:convert via Trim utility first")
    return p, {"asset_id": int(asset_id), "filename": filename}


def _gain_pan(track: Dict[str, Any]) -> tuple[float, float]:
    amp = 10 ** (float(track.get("gain_db") or 0.0) / 20.0)
    pan = max(-1.0, min(1.0, float(track.get("pan") or 0.0)))
    left_mul = amp * (1.0 if pan <= 0 else (1.0 - pan))
    right_mul = amp * (1.0 if pan >= 0 else (1.0 + pan))
    return left_mul, right_mul


def _read_wav(path: Path) -> tuple[list[list[float]], int]:
    with wave.open(str(path), "rb") as w:
        n_channels = w.getnchannels()
        sample_rate = w.getframerate()
        sampwidth = w.getsampwidth()
        n_frames = w.getnframes()
        raw = w.readframes(n_frames)
    if sampwidth != 2:
        raise ValueError(f"unsupported_wav_bit_depth:{sampwidth * 8}")
    import array
    pcm = array.array("h")
    pcm.frombytes(raw)
    if n_channels <= 0:
        raise ValueError("invalid_wav_channels")
    if n_channels == 1:
        l = [s / 32768.0 for s in pcm]
        r = l.copy()
    else:
        l, r = [], []
        for i in range(0, len(pcm), n_channels):
            vals = pcm[i:i + n_channels]
            left = vals[0]
            right = vals[1] if len(vals) > 1 else vals[0]
            l.append(left / 32768.0)
            r.append(right / 32768.0)
    return [l, r], sample_rate


def _write_wav(path: Path, stereo: list[list[float]], sample_rate: int) -> None:
    import array
    l, r = stereo
    n = min(len(l), len(r))
    pcm = array.array("h")
    for i in range(n):
        lv = int(max(-1.0, min(1.0, l[i])) * 32767)
        rv = int(max(-1.0, min(1.0, r[i])) * 32767)
        pcm.extend([lv, rv])
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm.tobytes())


def mix_audio(
    conn: sqlite3.Connection,
    dirs: Dict[str, Path],
    project_code: str,
    bus_name: str,
    tracks: List[Dict[str, Any]],
    created_at: str,
    output_kind: str = "mix",
) -> Dict[str, Any]:
    valid_tracks = [t for t in (tracks or []) if (t.get("asset_id") is not None)]
    if not valid_tracks:
        raise ValueError("no_tracks_assigned")

    resolved = []
    for t in valid_tracks:
        path, base = _resolve_asset_audio_path(conn, dirs, project_code, int(t.get("asset_id")))
        left_mul, right_mul = _gain_pan(t)
        resolved.append({
            **base,
            "path": path,
            "gain_db": float(t.get("gain_db") or 0.0),
            "pan": max(-1.0, min(1.0, float(t.get("pan") or 0.0))),
            "start_ms": max(0, int(float(t.get("start_ms") or 0))),
            "duration_ms": max(0, int(float(t.get("duration_ms") or 0))),
            "left_mul": left_mul,
            "right_mul": right_mul,
        })

    ffmpeg_bin = shutil.which("ffmpeg")
    prefix = "TIMELINE" if output_kind == "timeline_mix" else "MIX"
    out_name = f"{prefix}_{project_code}_{bus_name}_{created_at.replace(':', '').replace('-', '').replace('Z', '')[-12:]}.wav"
    out_name = "".join(c if c.isalnum() or c in "._-" else "_" for c in out_name)
    out_path = dirs["exports"] / out_name

    if ffmpeg_bin:
        cmd = [ffmpeg_bin, "-y"]
        filters = []
        mix_inputs = []
        for i, t in enumerate(resolved):
            cmd += ["-i", str(t["path"])]
            trim = ""
            if t["duration_ms"] > 0:
                trim = f",atrim=start=0:end={t['duration_ms']/1000.0:.3f}"
            delay = int(max(0, t["start_ms"]))
            filters.append(
                f"[{i}:a]aformat=channel_layouts=stereo{trim},pan=stereo|c0=c0*{t['left_mul']:.6f}|c1=c1*{t['right_mul']:.6f},adelay={delay}|{delay}[a{i}]"
            )
            mix_inputs.append(f"[a{i}]")
        filters.append("".join(mix_inputs) + f"amix=inputs={len(mix_inputs)}:normalize=1[aout]")
        cmd += ["-filter_complex", ";".join(filters), "-map", "[aout]", str(out_path)]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(f"ffmpeg_mix_failed:{proc.stderr[-240:]}")
    else:
        stereo_mix = [[], []]
        sample_rate = None
        for t in resolved:
            (l, r), sr = _read_wav(t["path"])
            if sample_rate is None:
                sample_rate = sr
            elif sr != sample_rate:
                raise ValueError("wav_sample_rate_mismatch")
            if t["duration_ms"] > 0:
                keep = int((t["duration_ms"] / 1000.0) * sr)
                l = l[:keep]
                r = r[:keep]
            delay_samples = int((t["start_ms"] / 1000.0) * sr)
            end_len = delay_samples + len(l)
            if len(stereo_mix[0]) < end_len:
                pad = end_len - len(stereo_mix[0])
                stereo_mix[0].extend([0.0] * pad)
                stereo_mix[1].extend([0.0] * pad)
            for i in range(len(l)):
                idx = delay_samples + i
                stereo_mix[0][idx] += l[i] * t["left_mul"]
                stereo_mix[1][idx] += r[i] * t["right_mul"]
        peak = max([0.0] + [abs(v) for v in stereo_mix[0]] + [abs(v) for v in stereo_mix[1]])
        if peak > 0.99:
            scale = 0.99 / peak
            stereo_mix[0] = [v * scale for v in stereo_mix[0]]
            stereo_mix[1] = [v * scale for v in stereo_mix[1]]
        _write_wav(out_path, stereo_mix, int(sample_rate or 44100))

    payload = {
        "kind": output_kind,
        "bus_name": bus_name,
        "filename": out_name,
        "tracks": [
            {
                "asset_id": t["asset_id"],
                "gain_db": t["gain_db"],
                "pan": t["pan"],
                "start_ms": t.get("start_ms", 0),
                "duration_ms": t.get("duration_ms", 0),
            }
            for t in resolved
        ],
    }
    add_asset(conn, project_code, output_kind, json.dumps(payload, ensure_ascii=False, indent=2), created_at)
    return payload

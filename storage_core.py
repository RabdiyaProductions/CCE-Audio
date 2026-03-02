import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

SCHEMA_VERSION = 3


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

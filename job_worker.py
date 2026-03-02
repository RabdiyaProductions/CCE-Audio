import json
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

from cce_audio_core import parse_project_spec, build_pilot_pack, build_pack_for_kind, render_platform_packs, make_issue_ref, sha256_bytes, now_utc_iso
import storage_core as storage


class JobWorker:
    """Background worker for KR_STD Hub orders.

    - Polls the jobs table for queued jobs.
    - Generates an audio pack + export zip.
    - Updates job status and provides export filename.

    Disable with env DISABLE_WORKER=1.
    """

    def __init__(self, conn, base_dir: Path, llm=None):
        self.conn = conn
        self.base_dir = base_dir
        self.exports_dir = base_dir / "exports"
        self.exports_dir.mkdir(parents=True, exist_ok=True)
        self.llm = llm
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._loop, daemon=True)

    def start(self):
        if not self._t.is_alive():
            self._t.start()

    def stop(self):
        self._stop.set()

    def _loop(self):
        while not self._stop.is_set():
            try:
                self.tick(max_jobs=2)
            except Exception:
                pass
            time.sleep(0.75)

    def tick(self, max_jobs: int = 3):
        jobs = storage.list_jobs(self.conn, status="queued", limit=max_jobs)
        for j in jobs:
            job_id = j["id"]
            kind = j["kind"]
            payload = {}
            try:
                payload = json.loads(j.get("payload_json") or "{}")
            except Exception:
                payload = {"raw": j.get("payload_json")}

            storage.update_job(self.conn, job_id, status="running", updated_at=now_utc_iso())
            try:
                result = self._process(kind, payload)
                export_filename = self._export_job_pack(job_id, kind, payload, result)
                storage.update_job(
                    self.conn,
                    job_id,
                    status="completed",
                    result_json=json.dumps(result, ensure_ascii=False, indent=2),
                    export_filename=export_filename,
                    updated_at=now_utc_iso(),
                )
            except Exception as e:
                storage.update_job(
                    self.conn,
                    job_id,
                    status="failed",
                    result_json=json.dumps({"error": str(e)}, ensure_ascii=False),
                    updated_at=now_utc_iso(),
                )

    def _process(self, kind: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        # Minimal standard: accept a project spec block
        project = payload.get("project") or payload.get("spec") or {}
        if not isinstance(project, dict):
            project = {"notes": str(project)}
        spec = parse_project_spec(project)

        # Always generate the baseline pilot pack for contract compatibility.
        pilot = build_pilot_pack(spec, llm=self.llm)

        # Optional specialised pack (sonic_brand / podcast / voice / score_cue / sfx)
        extra_kind, extra_pack = build_pack_for_kind(kind, spec, llm=self.llm)
        extras = {}
        if extra_kind != "pilot_pack":
            extras[extra_kind] = extra_pack

        # Add optional job brief
        brief = (payload.get("brief") or "").strip()
        if brief:
            pilot.setdefault("job", {})
            pilot["job"]["brief"] = brief
            pilot["job"]["kind"] = kind
            for _, ep in extras.items():
                if isinstance(ep, dict):
                    ep.setdefault("job", {})
                    ep["job"]["brief"] = brief
                    ep["job"]["kind"] = kind

        result = {
            "job_id": payload.get("order_id") or payload.get("job_id") or None,
            "kind": kind,
            "pilot_pack": pilot,
            "generated_at": now_utc_iso(),
        }
        result.update(extras)
        return result
    def _export_job_pack(self, job_id: str, kind: str, payload: Dict[str, Any], result: Dict[str, Any]) -> str:
        # Build an export ZIP consistent with /api/export contract
        project_code = (payload.get("project_code") or f"JOB-{job_id[:10]}").upper()
        seq = len(storage.list_exports(self.conn, project_code)) + 1
        issue_ref = make_issue_ref("AUDIO", project_code, seq)

        export_meta = {
            "issue_ref": issue_ref,
            "project_code": project_code,
            "created_at": now_utc_iso(),
            "approved": True,
            "force": True,
            "job": {"id": job_id, "kind": kind},
        }

        pack = (result.get("pilot_pack") or {})
        platform_files = render_platform_packs(project_code, pack)

        import io, zipfile
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as z:
            z.writestr("issue_ref.txt", issue_ref)
            z.writestr("project.json", json.dumps({"project_code": project_code, "status": "Approved", "source": "job"}, indent=2))
            z.writestr("pilot_pack.json", json.dumps(pack, indent=2, ensure_ascii=False))
            # include other pack types if present
            for k, fname in (
                ("sonic_brand_pack", "sonic_brand_pack.json"),
                ("podcast_pack", "podcast_pack.json"),
                ("voice_pack", "voice_pack.json"),
                ("score_cue_pack", "score_cue_pack.json"),
                ("sfx_pack", "sfx_pack.json"),
            ):
                if k in result and isinstance(result.get(k), dict):
                    z.writestr(fname, json.dumps(result[k], indent=2, ensure_ascii=False))
            z.writestr("export_meta.json", json.dumps(export_meta, indent=2))
            z.writestr("WORKFLOW.md", "# CCE Audio Workflow (Internal)\n\n1) Hub submits order\n2) CCE Audio generates pack\n3) (Optional) Critique + founder signoff\n4) Export pack\n5) Hub schedules/publishes\n")
            for plat, files in platform_files.items():
                for rel, content in files.items():
                    z.writestr(f"platforms/{plat}/{rel}", content)
            # manifest
            manifest = {}
            for info in z.infolist():
                if info.is_dir():
                    continue
                data = z.read(info.filename)
                manifest[info.filename] = {"sha256": sha256_bytes(data), "bytes": len(data)}

            manifest["manifest.json"] = {"sha256": "", "bytes": 0}
            mb = json.dumps(manifest, indent=2).encode("utf-8")
            manifest["manifest.json"] = {"sha256": sha256_bytes(mb), "bytes": len(mb)}
            z.writestr("manifest.json", json.dumps(manifest, indent=2))

        blob = buf.getvalue()
        filename = f"{issue_ref}.zip"
        out_path = self.exports_dir / filename
        out_path.write_bytes(blob)
        storage.add_export(self.conn, project_code, issue_ref, filename, sha256_bytes(blob), len(blob), now_utc_iso())
        return filename
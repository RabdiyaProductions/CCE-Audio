import os, json, hashlib, zipfile, io, subprocess, shutil
from dataclasses import dataclass
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256_bytes(data: bytes) -> str:
    h = hashlib.sha256()
    h.update(data)
    return h.hexdigest()


def ensure_dirs(base: Path) -> Dict[str, Path]:
    dirs = {
        "data": base / "data",
        "exports": base / "exports",
        "imports": base / "imports",
        "uploads": base / "uploads",
        "outputs": base / "outputs",
        "logs": base / "logs",
        "tmp": base / "tmp",
    }
    for p in dirs.values():
        p.mkdir(parents=True, exist_ok=True)
    return dirs


def safe_filename(name: str) -> str:
    name = (name or "").strip().replace(" ", "_")
    name = "".join(ch for ch in name if ch.isalnum() or ch in ("_", "-", ".", "+"))
    return name[:180] or "file"


def make_issue_ref(prefix: str, project_code: str, seq: int) -> str:
    dt = datetime.now(timezone.utc).strftime("%Y%m%d")
    return f"{prefix}-{project_code}-{dt}-{seq:03d}"


@dataclass
class ProjectSpec:
    title: str
    studio: str
    audio_type: str
    genre: str
    bpm: str
    musical_key: str
    mood: str
    references: str
    lyrics_theme: str
    notes: str


DEFAULT_QUESTIONS = [
    {"key": "studio", "label": "Studio division", "hint": "Flux / Cosmic / Echelon / Parallax", "type": "select",
     "options": ["Flux", "Cosmic", "Echelon", "Parallax", "DJ Parallax"]},
    {"key": "audio_type", "label": "Audio type", "hint": "Song / Beat / Podcast / Voiceover", "type": "select",
     "options": ["Song", "Beat", "Score Cue", "Sonic Logo", "Podcast Episode", "Voiceover", "SFX Pack"]},
    {"key": "genre", "label": "Primary genre", "hint": "Hip-hop, drill, house, cinematic, lo-fi, etc.", "type": "text"},
    {"key": "bpm", "label": "BPM", "hint": "e.g. 92 / 128 / 140", "type": "text"},
    {"key": "musical_key", "label": "Key", "hint": "e.g. A minor / C# minor / F major", "type": "text"},
    {"key": "mood", "label": "Mood", "hint": "Dark / euphoric / chilled / aggressive / cinematic", "type": "text"},
    {"key": "references", "label": "Taste / reference notes", "hint": "Describe vibe + production references (no copying melodies).", "type": "textarea"},
    {"key": "lyrics_theme", "label": "Lyrics theme (optional)", "hint": "Topic, hook idea, safe constraints", "type": "textarea"},
    {"key": "notes", "label": "Extra direction", "hint": "Any constraints, deliverables, loudness target, etc.", "type": "textarea"},
]


def parse_project_spec(payload: Dict[str, Any]) -> ProjectSpec:
    # tolerate both form and API payloads
    title = (payload.get("title") or payload.get("name") or "Untitled").strip()[:120]
    return ProjectSpec(
        title=title,
        studio=(payload.get("studio") or "Flux").strip(),
        audio_type=(payload.get("audio_type") or "Song").strip(),
        genre=(payload.get("genre") or "Cinematic").strip(),
        bpm=(payload.get("bpm") or "120").strip(),
        musical_key=(payload.get("musical_key") or "A minor").strip(),
        mood=(payload.get("mood") or "Cinematic").strip(),
        references=(payload.get("references") or payload.get("reference_notes") or "").strip(),
        lyrics_theme=(payload.get("lyrics_theme") or "").strip(),
        notes=(payload.get("notes") or "").strip(),
    )


def _arrangement_sections(audio_type: str) -> List[Dict[str, Any]]:
    at = (audio_type or "").lower()

    # Podcast / speech
    if "podcast" in at:
        return [
            {"section": "Cold open", "bars": "0:00-0:20", "notes": "Hook / highlight"},
            {"section": "Intro", "bars": "0:20-1:00", "notes": "Theme + sponsor slot"},
            {"section": "Main segments", "bars": "1:00-18:00", "notes": "Chapters with markers + beds"},
            {"section": "Outro", "bars": "18:00-19:00", "notes": "CTA + credits"},
        ]

    # Sonic branding / audio logo
    if "sonic" in at or "logo" in at:
        return [
            {"section": "Signature hit", "bars": "0.0s-0.4s", "notes": "Immediate recognisable motif"},
            {"section": "Brand lift", "bars": "0.4s-1.4s", "notes": "Harmony / shimmer / swell"},
            {"section": "Resolve", "bars": "1.4s-2.2s", "notes": "Clean cadence; no mud"},
            {"section": "Tail", "bars": "2.2s-3.0s", "notes": "Optional reverb tail (platform dependent)"},
        ]

    # Film / media scoring cue
    if "score" in at or "cue" in at:
        return [
            {"section": "Establish", "bars": "0:00-0:20", "notes": "Palette + motif + pulse"},
            {"section": "Build", "bars": "0:20-0:55", "notes": "Risers, tension, dynamics"},
            {"section": "Peak / hit points", "bars": "0:55-1:15", "notes": "Sync to beats / edits / key hits"},
            {"section": "Resolve / button", "bars": "1:15-1:30", "notes": "Clean ending or sting"},
        ]

    # Voiceover / spoken ad
    if "voice" in at:
        return [
            {"section": "Slate", "bars": "0:00-0:03", "notes": "Project + take ID"},
            {"section": "Main read", "bars": "0:03-0:28", "notes": "Clear, paced, confident delivery"},
            {"section": "CTA", "bars": "0:28-0:35", "notes": "Call-to-action with emphasis"},
            {"section": "Alt takes", "bars": "0:35-0:55", "notes": "2–3 alt reads (tone / pace variants)"},
        ]

    # SFX pack
    if "sfx" in at or "fx pack" in at:
        return [
            {"section": "Impacts", "bars": "N/A", "notes": "Short hits; multiple intensities"},
            {"section": "Whooshes", "bars": "N/A", "notes": "Clean sweeps; no harsh top"},
            {"section": "Textures", "bars": "N/A", "notes": "Beds / drones / atmos"},
            {"section": "UI / beeps", "bars": "N/A", "notes": "Short, modern, brand-safe"},
        ]

    # Song/beat default
    return [
        {"section": "Hook", "bars": "1-8", "notes": "Instant motif; memorable rhythm"},
        {"section": "Verse 1", "bars": "9-24", "notes": "Build narrative / groove"},
        {"section": "Pre-chorus", "bars": "25-32", "notes": "Tension + lift"},
        {"section": "Chorus", "bars": "33-48", "notes": "Big payoff; simplify"},
        {"section": "Bridge", "bars": "49-56", "notes": "Contrast + reset"},
        {"section": "Final chorus", "bars": "57-72", "notes": "Variation + last hook"},
    ]


def build_pilot_pack(spec: ProjectSpec, llm: Optional[Any] = None) -> Dict[str, Any]:
    """Audio 'pilot pack' = production blueprint + deliverables.

    Offline-first deterministic baseline. If an LLM provider is supplied and enabled,
    the provider can enhance the pack.
    """
    base = {
        "title": spec.title,
        "studio": spec.studio,
        "audio_type": spec.audio_type,
        "genre": spec.genre,
        "bpm": spec.bpm,
        "key": spec.musical_key,
        "mood": spec.mood,
        "references": spec.references,
        "lyrics_theme": spec.lyrics_theme,
        "created_at": now_utc_iso(),
    }

    arrangement = _arrangement_sections(spec.audio_type)

    # Timeline (audio contract): clip_id + timing + notes.
    timeline = []
    for i, sec in enumerate(arrangement, start=1):
        timeline.append({
            "clip_id": f"CLIP-{i:02d}",
            "section": sec["section"],
            "bars": sec["bars"],
            "chords": "TBC",
            "melody": "TBC",
            "drums": "TBC",
            "sound_design": spec.mood,
            "vocal_notes": "TBC" if spec.lyrics_theme else "Instrumental",
            "mix_notes": "TBC",
            "notes": sec.get("notes", ""),
        })

    pack = {
        "base": base,
        "arrangement": arrangement,
        "timeline": timeline,
        "stems_plan": {
            "core": ["Drums", "Bass", "Chords", "Lead", "FX", "Vox" if spec.lyrics_theme else "(none)"]
        },
        "mix_plan": {
            "targets": {
                "lufs_integrated": "-14 (streaming)" if "podcast" not in spec.audio_type.lower() else "-16 (speech)",
                "true_peak_db": "-1.0",
            },
            "checks": ["gain staging", "EQ cleanup", "compression control", "stereo image", "reverb hygiene", "mono compatibility"],
        },
        "master_plan": {
            "chain": ["EQ", "Glue compression", "Saturation (light)", "Limiter"],
            "qc": ["no clipping", "no harsh sibilance", "consistent loudness", "metadata"],
        },
        "deliverables": [
            "WAV master",
            "MP3 deliverable",
            "Stems (optional)",
            "Cue sheet / credits",
            "Platform description + hashtags",
        ],
        "producer_notes": {
            "archetype": "Genre-appropriate composite producer (safe)",
            "pacing": "Hook early; reduce dead air; keep groove readable",
            "sound": "Tight low-end; clean mid; controlled top",
        },
    }

    if llm:
        try:
            pack = llm.enhance_pilot(pack)  # optional enhancement layer
        except Exception:
            pass
    return pack



def build_sonic_brand_pack(spec: ProjectSpec, llm: Optional[Any] = None) -> Dict[str, Any]:
    base = {
        "title": spec.title,
        "studio": spec.studio,
        "kind": "sonic_brand",
        "genre": spec.genre,
        "mood": spec.mood,
        "created_at": now_utc_iso(),
    }
    pack = {
        "base": base,
        "brief": (spec.notes or "").strip(),
        "intent": "Sonic branding / audio logo concepts and deliverables.",
        "concepts": [
            {"name": "Concept A", "motif": "3–5 note mnemonic", "texture": "clean + modern", "notes": "Fast recognisability"},
            {"name": "Concept B", "motif": "rhythmic hit + rise", "texture": "warm + confident", "notes": "Works on mobile speakers"},
            {"name": "Concept C", "motif": "sparkle + resolve", "texture": "premium + minimal", "notes": "Short tail; no mud"},
        ],
        "deliverables": [
            {"file": "audio_logo_0p5s.wav", "notes": "Ultra-short hit"},
            {"file": "audio_logo_2s.wav", "notes": "Primary logo"},
            {"file": "audio_logo_3s.wav", "notes": "Extended logo"},
            {"file": "stinger_1s.wav", "notes": "Transition sting"},
            {"file": "bed_10s.wav", "notes": "Optional underlay bed"},
        ],
        "mix_master": {
            "targets": {"lufs_integrated": "-14", "true_peak_db": "-1.0"},
            "notes": ["Optimise for small speakers", "Keep sub controlled", "Avoid harsh 3–6k"],
        },
        "usage_rights": [
            "Do not copy or imitate protected melodies/logos.",
            "Use original motifs and generic genre textures.",
        ],
    }
    if llm:
        try:
            out = llm._call_json(
                "Create a sonic branding pack for an audio logo. Return JSON with concepts, motif description, instrumentation, deliverables, and mix/master targets. Keep it brand-safe.",
                {"spec": spec.__dict__, "baseline": pack},
                max_output_tokens=1400,
            )
            if isinstance(out, dict):
                pack.update(out.get("pack") if isinstance(out.get("pack"), dict) else out)
        except Exception:
            pass
    return pack


def build_podcast_pack(spec: ProjectSpec, llm: Optional[Any] = None) -> Dict[str, Any]:
    base = {
        "title": spec.title,
        "studio": spec.studio,
        "kind": "podcast",
        "mood": spec.mood,
        "created_at": now_utc_iso(),
    }
    pack = {
        "base": base,
        "show_format": {
            "episode_length_min": 20,
            "structure": _arrangement_sections("podcast"),
            "tone": spec.mood,
        },
        "production": {
            "recording": ["room tone capture", "mic technique", "double-ender if remote"],
            "editing": ["noise reduction light", "de-ess", "tighten pauses", "chapter markers"],
            "mix": ["voice EQ cleanup", "compression for consistency", "music bed under intro/outro"],
            "master": ["-16 LUFS integrated (speech)", "-1.0 dBTP true peak"],
        },
        "deliverables": [
            "Episode WAV master",
            "Episode MP3 (podcast spec)",
            "Show notes draft",
            "Chapters / timecodes",
        ],
        "notes": (spec.notes or "").strip(),
    }
    if llm:
        try:
            out = llm._call_json(
                "Create a podcast episode production pack. Return JSON with segment plan, host direction, edit checklist, loudness targets, and show notes skeleton.",
                {"spec": spec.__dict__, "baseline": pack},
                max_output_tokens=1600,
            )
            if isinstance(out, dict):
                pack.update(out.get("pack") if isinstance(out.get("pack"), dict) else out)
        except Exception:
            pass
    return pack


def build_voice_pack(spec: ProjectSpec, llm: Optional[Any] = None) -> Dict[str, Any]:
    base = {
        "title": spec.title,
        "studio": spec.studio,
        "kind": "voice",
        "mood": spec.mood,
        "created_at": now_utc_iso(),
    }
    pack = {
        "base": base,
        "script": {
            "primary": "TBC — provide script text or brief for generation.",
            "alts": [],
            "pronunciations": [],
        },
        "direction": {
            "tone": spec.mood,
            "pace": "medium",
            "energy": "confident",
            "takes": 3,
        },
        "processing_chain": ["HPF", "EQ cleanup", "Compression", "De-ess", "Limiter (light)"],
        "deliverables": [
            "Dry voice WAV",
            "Processed voice WAV",
            "Alt takes WAV",
            "Final MP3",
        ],
        "notes": (spec.notes or "").strip(),
    }
    if llm:
        try:
            out = llm._call_json(
                "Create a voice production pack (voiceover). Return JSON with 2-3 script options if brief allows, direction, mic/room checklist, and deliverables.",
                {"spec": spec.__dict__, "baseline": pack},
                max_output_tokens=1400,
            )
            if isinstance(out, dict):
                pack.update(out.get("pack") if isinstance(out.get("pack"), dict) else out)
        except Exception:
            pass
    return pack


def build_score_cue_pack(spec: ProjectSpec, llm: Optional[Any] = None) -> Dict[str, Any]:
    base = {
        "title": spec.title,
        "studio": spec.studio,
        "kind": "score_cue",
        "genre": spec.genre,
        "mood": spec.mood,
        "created_at": now_utc_iso(),
    }
    pack = {
        "base": base,
        "cue": {
            "duration": "1:30",
            "structure": _arrangement_sections("score cue"),
            "instrumentation": ["Strings", "Low brass", "Percussion", "Synth textures"],
            "hit_points": ["TBC — provide timecodes or edit beats"],
        },
        "deliverables": [
            "Cue WAV master",
            "Stems: rhythm / harmonic / melodic / FX",
            "Cue sheet draft",
        ],
        "notes": (spec.notes or "").strip(),
    }
    if llm:
        try:
            out = llm._call_json(
                "Create a film/media score cue pack. Return JSON with structure, instrumentation, motif plan, hit points, and deliverables. Keep it production ready.",
                {"spec": spec.__dict__, "baseline": pack},
                max_output_tokens=1600,
            )
            if isinstance(out, dict):
                pack.update(out.get("pack") if isinstance(out.get("pack"), dict) else out)
        except Exception:
            pass
    return pack


def build_sfx_pack(spec: ProjectSpec, llm: Optional[Any] = None) -> Dict[str, Any]:
    base = {
        "title": spec.title,
        "studio": spec.studio,
        "kind": "sfx_pack",
        "mood": spec.mood,
        "created_at": now_utc_iso(),
    }
    pack = {
        "base": base,
        "categories": [
            {"name": "Impacts", "items": ["Impact_01", "Impact_02", "Impact_03"]},
            {"name": "Whooshes", "items": ["Whoosh_01", "Whoosh_02", "Whoosh_03"]},
            {"name": "Textures", "items": ["Texture_01", "Texture_02", "Texture_03"]},
            {"name": "UI", "items": ["UI_Click_01", "UI_Beep_01", "UI_Sweep_01"]},
        ],
        "format": {"sample_rate_hz": 48000, "bit_depth": 24, "mono_stereo": "stereo", "naming": "Category_Name_##.wav"},
        "deliverables": ["Foldered WAVs", "Index CSV", "Preview reel (optional)"],
        "notes": (spec.notes or "").strip(),
    }
    if llm:
        try:
            out = llm._call_json(
                "Create a sound effects pack plan. Return JSON with categories, item list, file format, naming, and deliverables.",
                {"spec": spec.__dict__, "baseline": pack},
                max_output_tokens=1400,
            )
            if isinstance(out, dict):
                pack.update(out.get("pack") if isinstance(out.get("pack"), dict) else out)
        except Exception:
            pass
    return pack


def build_pack_for_kind(kind: str, spec: ProjectSpec, llm: Optional[Any] = None) -> Tuple[str, Dict[str, Any]]:
    k = (kind or "").lower()
    # If hub sends a generic audio job, route based on the project's audio_type.
    if k in ("audio_job", "auto") or "audio_job" in k:
        k = (spec.audio_type or "").lower()
    if "sonic" in k:
        return ("sonic_brand_pack", build_sonic_brand_pack(spec, llm=llm))
    if "podcast" in k:
        return ("podcast_pack", build_podcast_pack(spec, llm=llm))
    if "voice" in k:
        return ("voice_pack", build_voice_pack(spec, llm=llm))
    if "score" in k or "cue" in k:
        return ("score_cue_pack", build_score_cue_pack(spec, llm=llm))
    if "sfx" in k or "fx" in k:
        return ("sfx_pack", build_sfx_pack(spec, llm=llm))
    # default
    return ("pilot_pack", build_pilot_pack(spec, llm=llm))

def render_platform_packs(project_code: str, pilot_pack: Dict[str, Any]) -> Dict[str, Dict[str, str]]:
    """Return dict: platform -> {relative_path: content_string}."""
    base = pilot_pack.get("base") or {}
    title = base.get("title", "Untitled")
    genre = base.get("genre", "")
    mood = base.get("mood", "")
    bpm = base.get("bpm", "")
    key = base.get("key", "")

    tiktok = {
        "caption.md": f"# TikTok / Reels Caption\n\n**{title}** — {genre} / {mood}\n\nBPM: {bpm} | Key: {key}\n\nHook idea: 2–3 seconds of motif + drop.\n\nHashtags: #music #producer #newmusic #{genre.replace(' ','')} #fyp\n",
        "shotlist.md": "- 0-2s: quick studio flash\n- 2-7s: hook playback with waveform\n- 7-12s: breakdown of drums/bass\n- 12-15s: CTA: link in bio\n",
    }

    spotify = {
        "description.md": f"# Spotify Description\n\n{title} is a {genre} piece with a {mood} palette.\n\nBPM: {bpm} | Key: {key}.\n\nCredits: (fill)\n",
        "canvas_idea.md": "Looping 8s canvas idea: minimal motion + title lock-up + beat-synced cuts.",
    }

    youtube = {
        "title_desc.md": f"# YouTube Title + Description\n\nTitle: {title} ({genre}) | {bpm} BPM\n\nDescription:\n- Mood: {mood}\n- BPM/Key: {bpm} / {key}\n- Credits: (fill)\n- Links: (fill)\n",
        "chapters.md": "(If podcast) add chapter markers here.",
    }

    hub = {
        "handoff.md": "This is an internal CCE Audio export. Studio hubs ingest and schedule/publish.",
    }

    deliverables = render_deliverables_files(project_code, pilot_pack)

    return {
        "deliverables": deliverables,
        "tiktok": tiktok,
        "spotify": spotify,
        "youtube": youtube,
        "hub_handoff": hub,
    }


def analyze_audio_file(path: Path) -> Dict[str, Any]:
    """Best-effort local analysis without heavy deps.

    - WAV: uses builtin wave.
    - Other formats: tries ffprobe if available.
    """
    out = {
        "path": str(path),
        "filename": path.name,
        "bytes": path.stat().st_size if path.exists() else None,
        "sha256": sha256_bytes(path.read_bytes()) if path.exists() else None,
        "duration_sec": None,
        "sample_rate": None,
        "channels": None,
        "codec": None,
        "mode": "unknown",
    }

    try:
        import wave
        if path.suffix.lower() == ".wav":
            with wave.open(str(path), "rb") as w:
                frames = w.getnframes()
                rate = w.getframerate()
                out["duration_sec"] = round(frames / float(rate), 3) if rate else None
                out["sample_rate"] = rate
                out["channels"] = w.getnchannels()
                out["codec"] = "pcm"
                out["mode"] = "wave"
            return out
    except Exception:
        pass

    # ffprobe fallback
    try:
        ffprobe_bin = os.environ.get("CCE_FFPROBE") or shutil.which("ffprobe")
        if not ffprobe_bin:
            raise RuntimeError("ffprobe_not_found")

        cmd = [ffprobe_bin, "-v", "error", "-show_entries", "format=duration:stream=codec_name,sample_rate,channels", "-of", "json", str(path)]
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=6)
        if p.returncode == 0 and p.stdout.strip():
            j = json.loads(p.stdout)
            fmt = (j.get("format") or {})
            dur = fmt.get("duration")
            out["duration_sec"] = round(float(dur), 3) if dur else None
            streams = j.get("streams") or []
            if streams:
                s0 = streams[0]
                out["codec"] = s0.get("codec_name")
                try:
                    out["sample_rate"] = int(s0.get("sample_rate")) if s0.get("sample_rate") else None
                except Exception:
                    out["sample_rate"] = None
                out["channels"] = s0.get("channels")
            out["mode"] = "ffprobe"
            return out
    except Exception:
        pass

    out["mode"] = "no_decoder"
    return out


def ffmpeg_available() -> bool:
    ffmpeg_bin = os.environ.get("CCE_FFMPEG") or shutil.which("ffmpeg")
    return bool(ffmpeg_bin)


def ffprobe_available() -> bool:
    ffprobe_bin = os.environ.get("CCE_FFPROBE") or shutil.which("ffprobe")
    return bool(ffprobe_bin)


def _ffmpeg_bin() -> Optional[str]:
    return os.environ.get("CCE_FFMPEG") or shutil.which("ffmpeg")


def _ffprobe_bin() -> Optional[str]:
    return os.environ.get("CCE_FFPROBE") or shutil.which("ffprobe")


def ensure_audio_wav(in_path: Path, tmp_dir: Path, sample_rate: int = 48000, channels: int = 2) -> Path:
    """Ensure audio is available as WAV.

    - If input is WAV, returns it.
    - Otherwise converts using ffmpeg into tmp_dir.
    """
    if in_path.suffix.lower() == ".wav":
        return in_path
    ffmpeg_bin = _ffmpeg_bin()
    if not ffmpeg_bin:
        raise RuntimeError("ffmpeg_required_for_non_wav")

    tmp_dir.mkdir(parents=True, exist_ok=True)
    out_path = tmp_dir / safe_filename(f"CONV_{in_path.stem}_{os.urandom(4).hex()}.wav")
    cmd = [
        ffmpeg_bin,
        "-y",
        "-i",
        str(in_path),
        "-acodec",
        "pcm_s16le",
        "-ar",
        str(int(sample_rate)),
        "-ac",
        str(int(channels)),
        str(out_path),
    ]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=18)
    if p.returncode != 0 or not out_path.exists():
        raise RuntimeError("ffmpeg_convert_failed")
    return out_path


def wav_peak_rms_dbfs(path: Path) -> Dict[str, Any]:
    """Compute simple peak and RMS dBFS for WAV using builtin wave.

    This is an approximation (LUFS != RMS). Used as fallback when ffmpeg ebur128 isn't available.
    """
    import wave
    import math
    import struct

    if path.suffix.lower() != ".wav":
        raise ValueError("wav_required")

    peak = 0.0
    sum_sq = 0.0
    count = 0

    with wave.open(str(path), "rb") as w:
        nch = w.getnchannels()
        sw = w.getsampwidth()
        if sw not in (1, 2):
            # unsupported widths -> bail to neutral
            return {"peak_dbfs": None, "rms_dbfs": None, "channels": nch, "sample_width": sw}
        fmt = "<" + ("b" if sw == 1 else "h")
        max_val = float(127 if sw == 1 else 32767)
        chunk = 4096
        while True:
            frames = w.readframes(chunk)
            if not frames:
                break
            # unpack interleaved samples
            step = sw
            for i in range(0, len(frames), step):
                samp = struct.unpack_from(fmt, frames, i)[0]
                v = abs(float(samp)) / max_val
                if v > peak:
                    peak = v
                sum_sq += v * v
                count += 1

    if count <= 0:
        return {"peak_dbfs": None, "rms_dbfs": None}

    rms = math.sqrt(sum_sq / float(count))
    peak_dbfs = 20.0 * math.log10(max(peak, 1e-12))
    rms_dbfs = 20.0 * math.log10(max(rms, 1e-12))
    return {"peak_dbfs": round(peak_dbfs, 2), "rms_dbfs": round(rms_dbfs, 2)}


def _parse_ebur128(stderr_text: str) -> Tuple[Optional[float], Optional[float]]:
    """Parse ffmpeg ebur128 output.

    Returns (integrated_lufs, true_peak_dbfs).
    """
    import re

    integ = None
    tp = None

    # Prefer summary lines if present
    for line in stderr_text.splitlines():
        line = line.strip()
        m = re.search(r"Integrated loudness:\s*([-0-9.]+)\s*LUFS", line)
        if m:
            try:
                integ = float(m.group(1))
            except Exception:
                pass
        m2 = re.search(r"True peak:\s*([-0-9.]+)\s*dBFS", line)
        if m2:
            try:
                tp = float(m2.group(1))
            except Exception:
                pass

    # Fallback: take last frame log entry containing I: and TP:
    if integ is None or tp is None:
        for line in stderr_text.splitlines()[::-1]:
            if " I:" in line and " TP:" in line:
                m = re.search(r"\bI:\s*([-0-9.]+)\s*LUFS", line)
                if m and integ is None:
                    try:
                        integ = float(m.group(1))
                    except Exception:
                        pass
                m2 = re.search(r"\bTP:\s*([-0-9.]+)\s*dBFS", line)
                if m2 and tp is None:
                    try:
                        tp = float(m2.group(1))
                    except Exception:
                        pass
                if integ is not None and tp is not None:
                    break

    return integ, tp


def loudness_measure(path: Path) -> Dict[str, Any]:
    """Measure integrated loudness and true peak.

    - If ffmpeg is available: uses ebur128 for LUFS + TP.
    - Otherwise: WAV-only RMS approximation.
    """
    ffmpeg_bin = _ffmpeg_bin()
    if ffmpeg_bin:
        cmd = [ffmpeg_bin, "-hide_banner", "-nostats", "-i", str(path), "-filter_complex", "ebur128=peak=true", "-f", "null", "-"]
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=24)
        stderr = (p.stderr or "")
        integ, tp = _parse_ebur128(stderr)
        if integ is not None or tp is not None:
            return {
                "method": "ffmpeg_ebur128",
                "integrated_lufs": round(integ, 2) if integ is not None else None,
                "true_peak_dbfs": round(tp, 2) if tp is not None else None,
                "ffmpeg": ffmpeg_bin,
            }

    # fallback
    if path.suffix.lower() != ".wav":
        return {"method": "unavailable", "error": "ffmpeg_required_for_non_wav"}

    pr = wav_peak_rms_dbfs(path)
    # RMS isn't LUFS, but it's better than nothing for a local QC gate.
    return {
        "method": "wav_rms_approx",
        "integrated_lufs": pr.get("rms_dbfs"),
        "true_peak_dbfs": pr.get("peak_dbfs"),
        "note": "Approximate: LUFS requires gating; RMS proxy used because ffmpeg not available.",
    }


def loudness_qc_report(path: Path, target_profile: str = "streaming_general") -> Dict[str, Any]:
    targets = default_loudness_targets()
    t = targets.get(target_profile) or targets.get("streaming_general")
    m = loudness_measure(path)
    lufs_i = m.get("integrated_lufs")
    tp = m.get("true_peak_dbfs")
    target_lufs = (t or {}).get("target_lufs_i")
    target_tp = (t or {}).get("true_peak_db")

    # Evaluate pass/fail with a pragmatic tolerance window.
    pass_lufs = None
    pass_tp = None
    if isinstance(lufs_i, (int, float)) and isinstance(target_lufs, (int, float)):
        pass_lufs = abs(float(lufs_i) - float(target_lufs)) <= 1.5
    if isinstance(tp, (int, float)) and isinstance(target_tp, (int, float)):
        pass_tp = float(tp) <= float(target_tp) + 0.2

    ok = True
    if pass_lufs is False or pass_tp is False:
        ok = False
    if m.get("method") == "unavailable":
        ok = False

    report = {
        "ok": ok,
        "target_profile": target_profile,
        "target": t,
        "measured": {
            "integrated_lufs": lufs_i,
            "true_peak_dbfs": tp,
            "method": m.get("method"),
        },
        "pass": {
            "lufs": pass_lufs,
            "true_peak": pass_tp,
        },
        "input": analyze_audio_file(path),
        "created_utc": now_utc_iso(),
        "notes": m.get("note") or "",
        "error": m.get("error") if m.get("method") == "unavailable" else "",
    }
    return report



# -----------------------------
# P11 Audio Utility + Deliverables
# -----------------------------

def default_loudness_targets() -> Dict[str, Any]:
    """Pragmatic defaults (not a substitute for platform-specific specs).

    Values are typical industry targets and may vary by distributor/platform.
    """
    return {
        "streaming_general": {"target_lufs_i": -14, "true_peak_db": -1.0, "notes": "Common for Spotify/YouTube-style loudness normalization"},
        "podcast_stereo": {"target_lufs_i": -16, "true_peak_db": -1.0, "notes": "Typical spoken word target"},
        "podcast_mono": {"target_lufs_i": -19, "true_peak_db": -1.0, "notes": "Typical spoken word target"},
        "broadcast_ebu_r128": {"target_lufs_i": -23, "true_peak_db": -1.0, "notes": "EBU R128 style reference"},
        "cinema_trailer": {"target_lufs_i": -24, "true_peak_db": -2.0, "notes": "Conservative default"},
        "tiktok_reels": {"target_lufs_i": -14, "true_peak_db": -1.0, "notes": "Short-form social default"},
    }


def deliverables_pack(project_code: str, pilot_pack: Dict[str, Any]) -> Dict[str, Any]:
    base = pilot_pack.get("base") or {}
    title = base.get("title", "Untitled")
    genre = base.get("genre", "")

    naming = {
        "root": f"{project_code}_{safe_filename(title)}",
        "pattern": "{PROJECT}_{TITLE}_{TYPE}_{BPM}_{KEY}_{VERSION}",
        "examples": [
            f"{project_code}_{safe_filename(title)}_MASTER_120_AMIN_v01.wav",
            f"{project_code}_{safe_filename(title)}_STEM_DRUMS_120_AMIN_v01.wav",
            f"{project_code}_{safe_filename(title)}_ALT_SHORT_120_AMIN_v02.wav",
        ],
        "notes": "Keep filenames ASCII-safe; avoid spaces; use consistent versioning.",
    }

    qc = {
        "checklist": [
            "No clipping; true-peak within target.",
            "Phase/mono compatibility check (where relevant).",
            "Noise floor acceptable for voice/podcast deliverables.",
            "Start/end clean (no clicks), fades where required.",
            "Deliverables naming matches pattern.",
            "Metadata notes captured (genre/mood/BPM/key).",
        ],
        "genre": genre,
        "created_utc": now_utc_iso(),
    }

    return {
        "project_code": project_code,
        "title": title,
        "naming": naming,
        "loudness_targets": default_loudness_targets(),
        "qc": qc,
    }


def render_deliverables_files(project_code: str, pilot_pack: Dict[str, Any]) -> Dict[str, str]:
    d = deliverables_pack(project_code, pilot_pack)

    naming_md = "# Deliverables Naming\n\n" + \
        f"**Root:** `{d['naming']['root']}`\n\n" + \
        f"**Pattern:** `{d['naming']['pattern']}`\n\n" + \
        "## Examples\n" + "\n".join([f"- `{x}`" for x in d['naming']['examples']]) + "\n\n" + \
        f"{d['naming']['notes']}\n"

    qc_md = "# QC Checklist (Defaults)\n\n" + "\n".join([f"- [ ] {x}" for x in d["qc"]["checklist"]]) + "\n"

    # stems index (placeholder from timeline)
    tl = pilot_pack.get("timeline") or []
    rows = ["track,clip_id,start_sec,end_sec,notes"]
    for i, clip in enumerate(tl[:32], start=1):
        cid = clip.get("clip_id", f"CLIP{i:02d}")
        st = clip.get("start_sec", 0)
        en = clip.get("end_sec", 0)
        notes = (clip.get("notes") or "").replace("\n", " ").replace(",", ";")
        rows.append(f"T{i:02d},{cid},{st},{en},{notes}")
    stems_csv = "\n".join(rows) + "\n"

    return {
        "naming_conventions.md": naming_md,
        "loudness_targets.json": json.dumps(d["loudness_targets"], indent=2),
        "qc_checklist.md": qc_md,
        "stems_index.csv": stems_csv,
    }


def wav_trim(in_path: Path, start_sec: float, end_sec: float, out_path: Path) -> Dict[str, Any]:
    """Trim WAV to [start_sec, end_sec] inclusive-ish.

    Returns analysis dict for the output file.
    """
    import wave

    if in_path.suffix.lower() != ".wav":
        raise ValueError("wav_trim_only_supports_wav")

    with wave.open(str(in_path), "rb") as w:
        nframes = w.getnframes()
        rate = w.getframerate()
        nch = w.getnchannels()
        sw = w.getsampwidth()

        dur = (nframes / float(rate)) if rate else 0.0
        s = max(0.0, float(start_sec))
        e = min(float(end_sec), float(dur))
        if e <= s:
            raise ValueError("invalid_trim_range")

        s_frame = int(s * rate)
        e_frame = int(e * rate)
        s_frame = max(0, min(s_frame, nframes))
        e_frame = max(0, min(e_frame, nframes))
        w.setpos(s_frame)
        data = w.readframes(max(0, e_frame - s_frame))

        out_path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(out_path), "wb") as o:
            o.setnchannels(nch)
            o.setsampwidth(sw)
            o.setframerate(rate)
            o.writeframes(data)

    return analyze_audio_file(out_path)


def wav_split_by_markers(in_path: Path, markers_sec: List[float], out_dir: Path, base_name: str) -> List[Dict[str, Any]]:
    import wave

    if in_path.suffix.lower() != ".wav":
        raise ValueError("wav_split_only_supports_wav")

    with wave.open(str(in_path), "rb") as w:
        nframes = w.getnframes()
        rate = w.getframerate()
        nch = w.getnchannels()
        sw = w.getsampwidth()
        dur = (nframes / float(rate)) if rate else 0.0

        # normalize markers
        marks = []
        for m in (markers_sec or []):
            try:
                v = float(m)
                if 0.0 < v < dur:
                    marks.append(v)
            except Exception:
                continue
        marks = sorted(set([round(x, 3) for x in marks]))
        points = [0.0] + marks + [dur]

        out_dir.mkdir(parents=True, exist_ok=True)
        segments: List[Dict[str, Any]] = []

        for i in range(len(points) - 1):
            s = points[i]
            e = points[i + 1]
            if (e - s) <= 0.01:
                continue
            s_frame = int(s * rate)
            e_frame = int(e * rate)
            s_frame = max(0, min(s_frame, nframes))
            e_frame = max(0, min(e_frame, nframes))

            w.setpos(s_frame)
            data = w.readframes(max(0, e_frame - s_frame))

            seg_name = safe_filename(f"{base_name}_seg{i+1:02d}_{s:.2f}-{e:.2f}.wav")
            out_path = out_dir / seg_name
            with wave.open(str(out_path), "wb") as o:
                o.setnchannels(nch)
                o.setsampwidth(sw)
                o.setframerate(rate)
                o.writeframes(data)

            a = analyze_audio_file(out_path)
            a.update({"segment_index": i + 1, "start_sec": round(s, 3), "end_sec": round(e, 3)})
            segments.append(a)

    return segments


def stems_index_csv_from_segments(segments: List[Dict[str, Any]]) -> str:
    headers = ["segment_index", "filename", "start_sec", "end_sec", "duration_sec", "bytes", "sha256"]
    rows = [",".join(headers)]
    for s in segments:
        rows.append(",".join([
            str(s.get("segment_index", "")),
            str(s.get("filename", "")),
            str(s.get("start_sec", "")),
            str(s.get("end_sec", "")),
            str(s.get("duration_sec", "")),
            str(s.get("bytes", "")),
            str(s.get("sha256", "")),
        ]))
    return "\n".join(rows) + "\n"

import os
import json
from typing import Any, Dict, Optional


def _extract_text_from_response(resp: Any) -> str:
    """Extract text from OpenAI Responses API response (best-effort)."""
    txt = ""
    try:
        for o in getattr(resp, "output", []) or []:
            if getattr(o, "type", None) == "message":
                for c in getattr(o, "content", []) or []:
                    if getattr(c, "type", None) == "output_text":
                        txt += getattr(c, "text", "")
    except Exception:
        pass
    return (txt or "").strip()


class LLMProvider:
    """Optional OpenAI-backed enhancement layer (offline-first).

    This engine must work without OpenAI installed and without OPENAI_API_KEY.
    When enabled, we use the OpenAI Responses API and request JSON-only payloads.
    """

    def __init__(self):
        self.api_key = os.environ.get("OPENAI_API_KEY", "").strip()
        self.model = os.environ.get("OPENAI_MODEL", "gpt-4.1-mini").strip()

        self.openai_installed = False
        self.reason = ""
        try:
            import openai  # noqa: F401
            self.openai_installed = True
        except Exception as e:
            self.reason = f"openai_import_failed:{type(e).__name__}"

        self.enabled = bool(self.api_key) and self.openai_installed

    def status(self) -> Dict[str, Any]:
        return {
            "openai_installed": self.openai_installed,
            "openai_enabled": self.enabled,
            "model": self.model,
            "provider": "openai" if self.enabled else "offline",
            "reason": self.reason if (not self.openai_installed and self.api_key) else "",
        }

    def _call_json(self, task: str, payload: Dict[str, Any], max_output_tokens: int = 1600) -> Dict[str, Any]:
        from openai import OpenAI

        client = OpenAI(api_key=self.api_key)
        prompt = {
            "task": task,
            "rules": [
                "Return JSON only.",
                "No markdown.",
                "Do not output or imitate copyrighted lyrics/melodies.",
                "Keep outputs production-oriented and structured.",
            ],
            "payload": payload,
        }

        resp = client.responses.create(
            model=self.model,
            input=json.dumps(prompt, ensure_ascii=False),
            max_output_tokens=max_output_tokens,
        )
        txt = _extract_text_from_response(resp)
        try:
            obj = json.loads(txt)
            if isinstance(obj, dict):
                obj.setdefault("llm_used", True)
                return obj
        except Exception:
            pass
        return {"llm_used": True, "llm_parse_error": True, "raw": txt[:8000]}

    # --- Audio enhancement layer ---

    def enhance_pilot(self, pack: Dict[str, Any]) -> Dict[str, Any]:
        if not self.enabled:
            return pack
        try:
            result = self._call_json(
                "Enhance an audio production pilot pack. Improve arrangement timeline details, chord/melody/drum guidance, mix/master plans, and add deliverables. Keep it internally consistent.",
                {"pilot_pack": pack},
                max_output_tokens=2200,
            )
        except Exception:
            return pack
        if "pilot_pack" in result and isinstance(result["pilot_pack"], dict):
            out = result["pilot_pack"]
            out["llm_used"] = True
            return out
        if isinstance(result, dict) and all(k in result for k in ("base", "timeline")):
            result["llm_used"] = True
            return result
        pack["llm_used"] = True
        pack["llm_raw"] = result.get("raw", "")
        return pack

    def clarify(self, spec: Dict[str, Any]) -> Dict[str, Any]:
        if not self.enabled:
            qs = [
                "Is this a Song, Beat, Podcast, Voiceover, or SFX pack?",
                "Target genre + 2 reference vibes (no copying)?",
                "BPM and key?",
                "Vocal or instrumental? If vocal: theme + language?",
                "Deliverables needed (stems, instrumental, clean edit, etc.)?",
                "Platform targets (TikTok, Spotify, YouTube) and loudness targets?",
            ]
            return {"llm_used": False, "questions": qs, "assumptions": []}
        try:
            return self._call_json(
                "Given a partial audio creative spec, propose 6–12 high-leverage clarifying questions and a small set of safe assumptions if unanswered.",
                {"spec": spec},
                max_output_tokens=900,
            )
        except Exception:
            return {"llm_used": True, "questions": [], "assumptions": []}

    def director_notes(self, spec: Dict[str, Any], pack: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        # Keep method name for parity (audio = producer notes).
        if not self.enabled:
            return {
                "llm_used": False,
                "producer_archetype": "Genre-appropriate composite",
                "arrangement": "Hook early, keep drops readable",
                "sound": "Tight low end; controlled highs; clean mids",
                "mix": "Gain stage; EQ carve; compress tastefully; check mono",
                "master": "Streaming loudness; true peak control",
            }
        try:
            return self._call_json(
                "Act as a composite producer+mix engineer. Provide notes for arrangement, sound selection, mix priorities, and 3 concrete improvements.",
                {"spec": spec, "pilot_pack": pack},
                max_output_tokens=1100,
            )
        except Exception:
            return {"llm_used": True, "raw": ""}

    def critique(self, pack: Dict[str, Any]) -> Dict[str, Any]:
        if not self.enabled:
            return {}
        try:
            return self._call_json(
                "Critique this audio pilot pack for hook strength, arrangement clarity, mix/master realism, and platform fit. Return score 0-100, findings list, and recommended_next list.",
                {"pilot_pack": pack},
                max_output_tokens=900,
            )
        except Exception:
            return {}

    def regenerate_clip(self, pack: Dict[str, Any], clip_id: str, instruction: str) -> Dict[str, Any]:
        if not self.enabled:
            return {}
        try:
            return self._call_json(
                "Regenerate a single timeline section in the audio pack. Respect the instruction, preserve continuity, keep section scope similar. Return updated_clip object.",
                {"pilot_pack": pack, "clip_id": clip_id, "instruction": instruction},
                max_output_tokens=800,
            )
        except Exception:
            return {}

    def trailer_spec(self, pack: Dict[str, Any], seconds: int = 15) -> Dict[str, Any]:
        # Keep name for parity: for audio, this is a 10-15s hook/teaser spec.
        if not self.enabled:
            tl = pack.get("timeline") or []
            picks = tl[:2]
            return {
                "llm_used": False,
                "seconds": seconds,
                "teaser_sections": picks,
                "notes": "Offline teaser spec only (render downstream).",
            }
        try:
            return self._call_json(
                "Create a 10–15 second hook/teaser spec for social platforms based on the audio pilot pack. Return teaser_sections list and a caption hook.",
                {"pilot_pack": pack, "seconds": seconds},
                max_output_tokens=900,
            )
        except Exception:
            return {}

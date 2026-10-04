"""Translate anatomy / segment display labels via Anthropic (Claude).

Used as on-the-fly fallback when a checked-in locale lookup table misses.
Env: ANTHROPIC_API_KEY, optional ANTHROPIC_MODEL.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

from idc_nl_anthropic import anthropic_configured, env_anthropic_model

logger = logging.getLogger("hub")

_LOCALE_NAMES: Dict[str, str] = {
    "fr": "French",
    "es": "Spanish",
    "ca": "Catalan",
    "de": "German",
    "it": "Italian",
    "pt": "Portuguese",
    "ko": "Korean",
    "pl": "Polish",
    "uk": "Ukrainian",
    "cs": "Czech",
    "nl": "Dutch",
    "el": "Greek",
    "lv": "Latvian",
    "hu": "Hungarian",
    "lt": "Lithuanian",
    "ro": "Romanian",
    "no": "Norwegian",
}

SYSTEM_PROMPT = """You translate short radiology / anatomy segmentation labels
for a medical imaging UI (TotalSegmentator / SegDB style names).

Rules:
- Translate from English to the requested target language.
- Keep clinical meaning; prefer standard anatomical terms in that language.
- Preserve laterality words (left/right) and numbers (e.g. vertebra L3).
- Do not add explanations, punctuation, or quotes beyond the label itself.
- Reply with ONLY a JSON array of strings, same length and order as the input.
- No markdown fences.
"""


def segment_label_translate_configured() -> bool:
    return anthropic_configured()


def _parse_json_array(text: str) -> Optional[List[Any]]:
    raw = str(text or "").strip()
    if not raw:
        return None
    fence = re.search(r"```(?:json)?\s*(\[.*\])\s*```", raw, re.DOTALL)
    if fence:
        raw = fence.group(1).strip()
    if raw.startswith("[") and raw.endswith("]"):
        try:
            payload = json.loads(raw)
            return payload if isinstance(payload, list) else None
        except json.JSONDecodeError:
            pass
    start = raw.find("[")
    end = raw.rfind("]")
    if start >= 0 and end > start:
        try:
            payload = json.loads(raw[start : end + 1])
            return payload if isinstance(payload, list) else None
        except json.JSONDecodeError:
            return None
    return None


def translate_segment_labels(
    texts: List[str],
    target: str,
    source: str = "en",
) -> List[str]:
    """Translate English segment labels; one output string per input (fallback: original)."""
    if not anthropic_configured():
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not configured on the Slicer hub."
        )
    target = str(target or "").strip().lower()
    source = str(source or "en").strip().lower() or "en"
    if not target:
        raise ValueError("Missing target language")
    cleaned = [str(t or "").strip() for t in texts]
    if not cleaned:
        return []
    if target == "en" or target == source:
        return list(cleaned)

    lang = _LOCALE_NAMES.get(target, target)
    user = (
        f"Source language: {source}\n"
        f"Target language: {lang} ({target})\n"
        f"Labels JSON array:\n{json.dumps(cleaned, ensure_ascii=False)}"
    )

    import anthropic

    api_key = str(os.getenv("ANTHROPIC_API_KEY") or "").strip()
    client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()
    model = env_anthropic_model()
    try:
        msg = client.messages.create(
            model=model,
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user}],
        )
    except Exception:
        logger.exception("segment label translate anthropic request failed")
        raise

    parts: List[str] = []
    for block in getattr(msg, "content", None) or []:
        if getattr(block, "type", None) == "text":
            parts.append(str(getattr(block, "text", "") or ""))
    parsed = _parse_json_array("\n".join(parts))
    if not parsed or len(parsed) != len(cleaned):
        logger.warning(
            "segment label translate: bad Claude reply len=%s expected=%s",
            len(parsed) if parsed else 0,
            len(cleaned),
        )
        return list(cleaned)

    out: List[str] = []
    for i, original in enumerate(cleaned):
        t = parsed[i]
        if isinstance(t, str) and t.strip():
            out.append(t.strip())
        else:
            out.append(original)
    return out

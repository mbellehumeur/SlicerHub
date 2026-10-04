"""NL → structured IDC SearchQuery via Anthropic.

Claude emits filters or SQL JSON; the worklist executes against IDC REST.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger("hub")

DEFAULT_MODEL = "claude-sonnet-4-20250514"
DEFAULT_IDC_MCP_URL = "https://api.imaging.datacommons.cancer.gov/mcp"

# Curated attributes matching worklist CORE_FILTER_DEFS + ADD_FILTER_GROUPS.
CURATED_ATTRIBUTES = """
Known filter attributes (prefer these exact names):
- term: collection_id, Modality, BodyPartExamined, analysis_result_id,
  Manufacturer, license_short_name, StudyDate, PatientSex,
  ManufacturerModelName, sop_class_name
- range (numeric): instanceCount, series_size_MB

Use exact categorical values (e.g. Modality "CT", BodyPartExamined "LIVER").
Substring / free-text / joins / non-filter columns → queryType "sql".
"""

SYSTEM_PROMPT = """You translate natural-language Imaging Data Commons (IDC) search
requests into a structured query for our application. Do NOT call tools.
Do NOT return series rows. Reply with ONLY a JSON object (no markdown fences).

{attributes}

Prefer cohort filters when possible:
{{
  "queryType": "filters",
  "filters": [
    {{ "attribute": "Modality", "type": "term", "values": ["CT"] }},
    {{ "attribute": "BodyPartExamined", "type": "term", "values": ["LIVER"] }},
    {{ "attribute": "instanceCount", "type": "range", "min": 200, "max": 400 }}
  ],
  "limit": {max_rows}
}}

You may also use op-style filters; we normalize them:
{{ "attribute": "Modality", "op": "eq", "value": "CT" }}
{{ "attribute": "instanceCount", "op": "between", "min": 200, "max": 400 }}
Do not use op "contains" on filters — use queryType "sql" instead.

When filters cannot express the ask (substring match, joins, other columns):
{{
  "queryType": "sql",
  "sql": "SELECT SeriesInstanceUID, StudyInstanceUID, PatientID, collection_id, Modality, SeriesDescription, instanceCount, series_size_MB, series_aws_url, crdc_series_uuid, aws_bucket FROM index WHERE ... LIMIT {max_rows}",
  "limit": {max_rows}
}}

SQL rules: single read-only SELECT/WITH against DuckDB IDC index; main table is
`index`. Include SeriesInstanceUID (and StudyInstanceUID when possible).
Default limit is {max_rows} (clamp 1–100).
"""


def anthropic_configured() -> bool:
    return bool(str(os.getenv("ANTHROPIC_API_KEY") or "").strip())


def env_anthropic_model() -> str:
    raw = os.getenv("ANTHROPIC_MODEL", DEFAULT_MODEL)
    return str(raw or DEFAULT_MODEL).strip() or DEFAULT_MODEL


def env_idc_mcp_upstream_url() -> str:
    raw = os.getenv("HUB_IDC_MCP_UPSTREAM_URL", DEFAULT_IDC_MCP_URL)
    return str(raw or DEFAULT_IDC_MCP_URL).strip().rstrip("/")


def _parse_json_from_text(text: str) -> Optional[Dict[str, Any]]:
    raw = str(text or "").strip()
    if not raw:
        return None
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", raw, re.DOTALL)
    if fence:
        raw = fence.group(1).strip()
    if raw.startswith("{") and raw.endswith("}"):
        try:
            payload = json.loads(raw)
            return payload if isinstance(payload, dict) else None
        except json.JSONDecodeError:
            pass
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        try:
            payload = json.loads(raw[start : end + 1])
            return payload if isinstance(payload, dict) else None
        except json.JSONDecodeError:
            return None
    return None


def _block_type(block: Any) -> str:
    if isinstance(block, dict):
        return str(block.get("type") or "")
    return str(getattr(block, "type", "") or "")


def _block_get(block: Any, key: str, default=None):
    if isinstance(block, dict):
        return block.get(key, default)
    return getattr(block, key, default)


def _normalize_filter(raw: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(raw, dict):
        return None
    attribute = str(raw.get("attribute") or "").strip()
    if not attribute:
        return None

    ftype = str(raw.get("type") or "").strip().lower()
    op = str(raw.get("op") or "").strip().lower()

    if ftype == "range" or op in ("between", "gte", "lte", "range"):
        out: Dict[str, Any] = {"attribute": attribute, "type": "range"}
        if raw.get("min") is not None:
            try:
                out["min"] = float(raw["min"])
            except (TypeError, ValueError):
                pass
        if raw.get("max") is not None:
            try:
                out["max"] = float(raw["max"])
            except (TypeError, ValueError):
                pass
        if "gte" in raw and "min" not in out:
            try:
                out["min"] = float(raw["gte"])
            except (TypeError, ValueError):
                pass
        if "lte" in raw and "max" not in out:
            try:
                out["max"] = float(raw["lte"])
            except (TypeError, ValueError):
                pass
        if "min" in out or "max" in out:
            return out
        return None

    if op == "contains":
        # REST terms are equality/IN only — caller should use SQL.
        return None

    values: List[str] = []
    if isinstance(raw.get("values"), list):
        values = [str(v).strip() for v in raw["values"] if str(v).strip()]
    elif raw.get("value") is not None:
        text = str(raw.get("value")).strip()
        if text:
            values = [text]
    if not values:
        return None
    return {"attribute": attribute, "type": "term", "values": values}


def _normalize_query(payload: Dict[str, Any], max_rows: int) -> Dict[str, Any]:
    """Normalize Claude JSON into {queryType, filters|sql, limit}."""
    limit_raw = payload.get("limit", max_rows)
    try:
        limit = int(limit_raw)
    except (TypeError, ValueError):
        limit = max_rows
    limit = max(1, min(limit, 100))

    query_type = str(payload.get("queryType") or payload.get("query_type") or "").strip().lower()
    sql = str(payload.get("sql") or "").strip()

    if query_type == "sql" or (not query_type and sql and not payload.get("filters")):
        if not sql:
            raise ValueError("queryType sql requires a non-empty sql string")
        return {"queryType": "sql", "sql": sql, "limit": limit}

    filters_raw = payload.get("filters")
    if not isinstance(filters_raw, list):
        filters_raw = []
    filters: List[Dict[str, Any]] = []
    for item in filters_raw:
        normalized = _normalize_filter(item)
        if normalized:
            filters.append(normalized)

    if not filters and sql:
        return {"queryType": "sql", "sql": sql, "limit": limit}

    if not filters:
        raise ValueError(
            "Claude returned neither usable filters nor SQL. "
            "Try a more specific IDC query."
        )

    return {"queryType": "filters", "filters": filters, "limit": limit}


def _extract_text_and_query(response: Any, max_rows: int) -> Dict[str, Any]:
    text_parts: List[str] = []
    parsed: Optional[Dict[str, Any]] = None

    for block in _block_get(response, "content", []) or []:
        if _block_type(block) != "text":
            continue
        text = str(_block_get(block, "text") or "")
        if not text:
            continue
        text_parts.append(text)
        candidate = _parse_json_from_text(text)
        if candidate:
            parsed = candidate

    if not parsed:
        raise ValueError(
            "Anthropic did not return a parseable SearchQuery JSON object"
        )

    query = _normalize_query(parsed, max_rows)
    return {
        "query": query,
        "text": "\n".join(text_parts).strip(),
        "toolName": "anthropic",
    }


def search_idc_via_anthropic(prompt: str, max_rows: int = 20) -> Dict[str, Any]:
    """Ask Claude for a SearchQuery JSON. Sync; use to_thread in hub."""
    api_key = str(os.getenv("ANTHROPIC_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not configured on the Slicer hub"
        )

    try:
        import anthropic
    except ImportError as exc:
        raise RuntimeError(
            "anthropic package is not installed on the Slicer hub"
        ) from exc

    user_prompt = str(prompt or "").strip()
    if not user_prompt:
        raise ValueError("prompt is required")

    capped_rows = max(1, min(int(max_rows or 20), 100))
    client = anthropic.Anthropic(api_key=api_key)

    logger.info(
        "idc-nl anthropic nl-query prompt_len=%d max_rows=%d",
        len(user_prompt),
        capped_rows,
    )

    response = client.messages.create(
        model=env_anthropic_model(),
        max_tokens=2048,
        system=SYSTEM_PROMPT.format(
            max_rows=capped_rows,
            attributes=CURATED_ATTRIBUTES.strip(),
        ),
        messages=[{"role": "user", "content": user_prompt}],
    )

    result = _extract_text_and_query(response, capped_rows)
    query = result["query"]
    logger.info(
        "idc-nl anthropic nl-query done queryType=%s",
        query.get("queryType"),
    )
    return result

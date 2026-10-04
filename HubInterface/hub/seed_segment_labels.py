#!/usr/bin/env python3
"""One-shot: fill i18n/segment-labels/<locale>.json from en-keys.json via Claude.

Run from hub (loads .env):
  python seed_segment_labels.py --locale fr --out ../../../pw46/i18n/segment-labels/fr.json

Paths default relative to this file → pw46/i18n/segment-labels/.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
try:
    from dotenv import load_dotenv

    load_dotenv(os.path.join(_HERE, ".env"))
except ImportError:
    pass

# pw46 is sibling of hub-interface: .../src/pw46 and .../src/hub-interface/...
_PW46_I18N = os.path.normpath(
    os.path.join(_HERE, "..", "..", "..", "..", "pw46", "i18n", "segment-labels")
)

from segment_label_translate import (  # noqa: E402
    segment_label_translate_configured,
    translate_segment_labels,
)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--locale", default="fr")
    ap.add_argument(
        "--keys",
        default=os.path.join(_PW46_I18N, "en-keys.json"),
        help="JSON array of English labels",
    )
    ap.add_argument(
        "--out",
        default="",
        help="Output JSON map (default: <i18n>/segment-labels/<locale>.json)",
    )
    ap.add_argument("--batch", type=int, default=40)
    ap.add_argument(
        "--merge",
        action="store_true",
        help="Keep existing non-empty translations in out file",
    )
    args = ap.parse_args()
    out_path = args.out or os.path.join(_PW46_I18N, f"{args.locale}.json")

    if not segment_label_translate_configured():
        print("ANTHROPIC_API_KEY not set", file=sys.stderr)
        return 1

    with open(args.keys, encoding="utf-8") as f:
        keys = json.load(f)
    if not isinstance(keys, list):
        print("keys file must be a JSON array", file=sys.stderr)
        return 1
    english = [str(k).strip() for k in keys if str(k).strip()]

    existing: dict[str, str] = {}
    if args.merge and os.path.isfile(out_path):
        with open(out_path, encoding="utf-8") as f:
            raw = json.load(f)
        if isinstance(raw, dict):
            existing = {
                str(k).lower(): str(v).strip()
                for k, v in raw.items()
                if isinstance(v, str) and str(v).strip()
            }

    need = [e for e in english if e.lower() not in existing]
    print(f"locale={args.locale} total={len(english)} need={len(need)}", flush=True)

    batch = max(1, min(64, int(args.batch)))
    for i in range(0, len(need), batch):
        chunk = need[i : i + batch]
        print(f"  translating {i + 1}-{i + len(chunk)}…", flush=True)
        translated = translate_segment_labels(chunk, args.locale, "en")
        for en, fr in zip(chunk, translated):
            if fr and fr.strip() and fr.strip() != en:
                existing[en.lower()] = fr.strip()

    # Stable key order by English sort of original list
    ordered = {e.lower(): existing[e.lower()] for e in english if e.lower() in existing}
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(ordered, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(f"wrote {len(ordered)} entries → {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

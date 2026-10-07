#!/usr/bin/env python3
"""FleXray Hub product stub — local X-ray anatomy segmentation (not implemented yet).

Upstream: https://flexray.csail.mit.edu/

This module is a placeholder so the worklist Local AI catalog and Service Providers
UI have a script path analogous to TotalSegmentator / TorchXRayVision. Real
on-device inference (WebGPU / local runtime) will replace this stub later.

Standalone (when implemented) from ``HubInterface/``:

    python service_providers/products/flexray/flexray.py --local
"""

from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    _ = argv if argv is not None else sys.argv[1:]
    print(
        "FleXray Hub product stub: inference is not implemented yet. "
        "See https://flexray.csail.mit.edu/",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

# FleXray Hub product (stub)

Upstream: [FleXray](https://flexray.csail.mit.edu/) — universal clinical X-ray
anatomy segmentation (MIT CSAIL / MGH / HMS). Paper: [arXiv:2609.26756](https://arxiv.org/abs/2609.26756).

> **Status:** scaffold only. `products/flexray/flexray.py` does not run inference
> or subscribe to the hub yet. The worklist exposes FleXray under **Local Inference**
> (info card). Remote Inference / Hub job wiring will come later.

## Paths

| Field | Value |
|-------|--------|
| Product | `FLEXRAY` |
| Info JSON | `resource_servers/products/flexray/flexray.info.json` |
| Script | `resource_servers/products/flexray/flexray.py` |

Sync the info JSON into the hub JS client with:

```bash
# from SlicerHub-js-clients/ (or hub-js-client/)
node tools/sync_inference_info.js
```

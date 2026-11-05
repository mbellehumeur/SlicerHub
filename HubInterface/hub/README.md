# Slicer Hub (`hub`)

FastAPI Hub / FHIRcast hub. Run standalone:

```bash
pip install -e ../python_client
pip install -r requirements.txt
python hub.py --port 2018
```

Admin: http://127.0.0.1:2018/api/hub/admin

See [docs/HUB-README.md](docs/HUB-README.md) and [azure-webapp.md](azure-webapp.md).

**Fly.io (dual hubs):** same image, two apps — mock [`../fly.toml`](../fly.toml) (`bellehumeur-hub` → [bellehumeur-engineering.com](https://bellehumeur-engineering.com/)), OIDC [`../fly.oidc.toml`](../fly.oidc.toml) (`bellehumeur-hub-oidc` → [oidc.bellehumeur-engineering.com](https://oidc.bellehumeur-engineering.com/)). Sync SPA mounts (`make_zip.py --skip-build`), set secrets / certs (see [`deploy-fly.sh`](deploy-fly.sh)), then `./deploy-fly.sh mock|oidc|all`. Details: [docs/oidc.md](docs/oidc.md).

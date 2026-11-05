# Slicer Hub (`hub`)

FastAPI Hub / FHIRcast hub. Run standalone:

```bash
pip install -e ../python_client
pip install -r requirements.txt
python hub.py --port 2018
```

Admin: http://127.0.0.1:2018/api/hub/admin

See [docs/HUB-README.md](docs/HUB-README.md) and [azure-webapp.md](azure-webapp.md).

## Docker (GHCR)

Public images (no Docker Hub account required to pull):

| Tag | Contents |
|-----|----------|
| `ghcr.io/mbellehumeur/slicer-hub:slim` (also `:latest`) | Hub API + WebSockets only |
| `ghcr.io/mbellehumeur/slicer-hub:full` | Same + SPA mounts (`/`, `/worklist-client/`, `/ohif/`, …) |

### Run (mock auth)

```bash
docker pull ghcr.io/mbellehumeur/slicer-hub:slim
docker run --rm -p 8000:8000 \
  -e HUB_AUTH_MODE=mock \
  -e HUB_PUBLIC_BASE_URL=http://127.0.0.1:8000 \
  -e HUB_WS_KEEPALIVE=true \
  ghcr.io/mbellehumeur/slicer-hub:slim
```

Admin: http://127.0.0.1:8000/api/hub/admin  
Auth config: http://127.0.0.1:8000/api/hub/auth/config

### Run (OIDC)

```bash
docker run --rm -p 8000:8000 \
  -e HUB_AUTH_MODE=oidc \
  -e HUB_PUBLIC_BASE_URL=https://your.example.com \
  -e HUB_OIDC_REDIRECT_URI=https://your.example.com/oauth/callback \
  -e HUB_OIDC_ISSUER=https://YOUR_TENANT.us.auth0.com \
  -e HUB_OIDC_CLIENT_ID=... \
  -e HUB_OIDC_CLIENT_SECRET=... \
  -e HUB_JWT_SECRET=... \
  -e HUB_WS_KEEPALIVE=true \
  ghcr.io/mbellehumeur/slicer-hub:slim
```

See [docs/oidc.md](docs/oidc.md) for IdP setup.

### Build / publish locally

From `HubInterface/` (Docker Buildx required):

```bash
# slim only (no push)
./hub/publish-docker.sh slim --no-push

# slim + full → ghcr.io (docker login ghcr.io first)
./hub/publish-docker.sh all
```

Full image needs SPA trees under `hub/` (`python make_zip.py --skip-build`).  
CI: push a `hub-v*` tag or run **Publish Hub Docker image** via Actions (`workflow_dispatch`).

Dockerfiles: [`Dockerfile.slim`](Dockerfile.slim) (API), [`Dockerfile.fly`](Dockerfile.fly) (full / Fly).

## Fly.io (dual hubs)

Same image layout, two apps — mock [`../fly.toml`](../fly.toml) (`bellehumeur-hub` → [bellehumeur-engineering.com](https://bellehumeur-engineering.com/)), OIDC [`../fly.oidc.toml`](../fly.oidc.toml) (`bellehumeur-hub-oidc` → [oidc.bellehumeur-engineering.com](https://oidc.bellehumeur-engineering.com/)). Sync SPA mounts (`make_zip.py --skip-build`), set secrets / certs (see [`deploy-fly.sh`](deploy-fly.sh)), then `./deploy-fly.sh mock|oidc|all`. Details: [docs/oidc.md](docs/oidc.md).

# Hub auth — OIDC (browser) + mock mode

The hub supports two auth modes via `HUB_AUTH_MODE`:

| Mode | Login | API Bearer |
|------|--------|------------|
| `mock` (default) | `POST /oauth/authorize` → `POST /oauth/token` | Optional (`HUB_REQUIRE_AUTH`) |
| `oidc` | Browser: `GET /oauth/login` → IdP → `GET /oauth/callback` | **Required**; Bearer = hub-minted session JWT |

In `oidc` mode, mock `/oauth/authorize` and `/oauth/token` return **503**.

Personal Google accounts work (not Workspace-only). Topic defaults to the Google **email** claim.

## Browser flow

```mermaid
sequenceDiagram
  participant JS as HubClient_JS
  participant Hub
  participant Google

  JS->>Hub: GET /api/hub/auth/config
  Hub-->>JS: mode=oidc login_url
  JS->>Hub: GET /oauth/login?return_to=...
  Hub->>Google: 302 authorize PKCE
  Google->>Hub: GET /oauth/callback?code
  Hub->>Hub: exchange verify mint JWT topic=email
  Hub->>JS: redirect return_to#access_token&topic&...
  JS->>JS: store token topic userName
  JS->>Hub: POST /api/hub Bearer subscribe
```

Callback fragment fields: `access_token`, `id_token`, `topic`, `user_name`, `expires_in`, `hub_auth=oidc`.

In oidc mode, subscribe/publish `hub.topic` must match the JWT `topic` claim.

## Environment

```bash
HUB_AUTH_MODE=oidc
HUB_OIDC_ISSUER=https://YOUR_TENANT.us.auth0.com
HUB_OIDC_CLIENT_ID=...
HUB_OIDC_CLIENT_SECRET=...
HUB_OIDC_REDIRECT_URI=https://<your-app>.azurewebsites.net/oauth/callback
HUB_PUBLIC_BASE_URL=https://<your-app>.azurewebsites.net
HUB_JWT_SECRET=<long random>
HUB_OIDC_TOPIC_CLAIM=email
HUB_OIDC_SCOPES=openid email profile
# Optional: allow SPA origins for return_to (localhost is always allowed)
# HUB_OIDC_RETURN_ORIGINS=https://app.example.com
```

| Variable | Purpose |
|----------|---------|
| `HUB_AUTH_MODE` | `mock` or `oidc` |
| `HUB_OIDC_ISSUER` | IdP issuer (Auth0 domain, Entra tenant, or `https://accounts.google.com`) |
| `HUB_OIDC_CLIENT_ID` / `HUB_OIDC_CLIENT_SECRET` | OAuth web client credentials |
| `HUB_OIDC_REDIRECT_URI` | Must match the IdP “callback URL” exactly (else derived from public base + `/oauth/callback`) |
| `HUB_PUBLIC_BASE_URL` | Public hub URL for redirects / auth config |
| `HUB_JWT_SECRET` | HS256 secret for hub session JWTs (**required** in oidc; not the insecure default) |
| `HUB_OIDC_TOPIC_CLAIM` | Claim used as hub topic (default `email`) |
| `HUB_OIDC_SCOPES` | Default `openid email profile` |
| `HUB_OIDC_JWKS_URL` | Optional override; otherwise JWKS from OIDC discovery |
| `HUB_OIDC_RETURN_ORIGINS` | Comma-separated SPA origins allowed for `return_to` |

Startup fails if `HUB_AUTH_MODE=oidc` and issuer/client id/secret/JWT secret are missing.

Discovery: `GET {issuer}/.well-known/openid-configuration` (works for Auth0, Google, Entra).

## Auth0 (recommended starter)

Often the simplest path: free developer tenant, one Regular Web Application, explicit callback URLs.

1. Sign up at [auth0.com](https://auth0.com/) (use any email, including `@bellehumeur-engineering.com`).
2. **Applications → Create Application → Regular Web Application**.
3. **Settings**: copy Domain, Client ID, Client Secret.
4. **Allowed Callback URLs** (comma-separated), matching hub exactly, e.g.  
   `https://<your-app>.azurewebsites.net/oauth/callback` and/or `http://127.0.0.1:2018/oauth/callback`.
5. **Allowed Logout URLs** / **Allowed Web Origins**: your hub public base (and local origin if testing).
6. Hub env: `HUB_OIDC_ISSUER=https://<Domain>` (no path; include region subdomain if Auth0 shows one, e.g. `xxx.us.auth0.com`).
7. Topic = Auth0 user **email** with `HUB_OIDC_TOPIC_CLAIM=email`.

You can later enable Google / Microsoft social connections inside Auth0 without changing hub code.

## Google Cloud Console (personal account)

1. [Google Cloud Console](https://console.cloud.google.com/) → APIs & Services → Credentials → Create OAuth client ID → **Web application**.
2. Authorized redirect URI must match hub exactly, e.g. `http://127.0.0.1:2018/oauth/callback` (local) and/or `https://<hub-host>/oauth/callback` (Azure).
3. OAuth consent screen: External; while **Testing**, add your Gmail under **Test users**.
4. Scopes `openid email profile` are non-sensitive for personal testing.
5. Hub topic becomes your Google email (`HUB_OIDC_TOPIC_CLAIM=email`).

## Public discovery

`GET /api/hub/auth/config` returns:

- mock: `{ "mode": "mock", "authorize_url", "token_url", "require_auth" }`
- oidc: `{ "mode": "oidc", "login_url", "issuer", "require_auth": true }`

## JS HubClient (`@slicer-hub/client`)

1. `authenticate()` fetches auth config.
2. Mock: existing POST authorize + `getToken(code)`.
3. OIDC: redirects to `/oauth/login?return_to=<SPA URL>`; on return, parses hash tokens into `model.hub.token`, topic, and userName; `getToken` is a no-op when tokens are already present.

Prefer full-page redirect with `return_to` (popup optional later for embedded viewers).

## Python `hub_client` — deferred

[`SlicerHubClient.authenticate`](../../python_client/src/hub_client/client.py) still uses mock `POST /oauth/authorize`. Against an oidc hub that call gets **503** until a later device/browser flow is added. Use mock mode or the JS client for OIDC login today.

## Verify checklist

1. Mock: `smoke_hub_http` / JS mock login still works.
2. OIDC without Google env: hub exits at startup.
3. OIDC + Google: HubClient → callback → Bearer subscribe/publish OK; no Bearer → 401.
4. Mock authorize under oidc → 503.
5. Python client against oidc hub → expected fail until deferred work.

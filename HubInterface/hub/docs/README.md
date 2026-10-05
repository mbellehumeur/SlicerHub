# Slicer Hub (`hub`)

FastAPI implementation of a [FHIRcast](https://fhircast.org/)-style hub for the Slicer Hub stack (VolView, vtk-js HubClient, OHIF hub extension). Subscribers connect over WebSocket (`/bind/{endpoint}`) or WebSub callbacks; the hub fans out events and collates typed request/response traffic.

Run standalone:

```bash
cd HubInterface/hub
pip install -e ../python_client
pip install -r requirements.txt
python hub.py --port 2018
```

Or with VolView RPC:

```bash
poetry run python server_with_hub.py --port 4014
```

## Core endpoints

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/api/hub/` | Subscribe, unsubscribe, or publish (body with `hub.mode` or `event`) |
| `POST` | `/api/hub/request` | Typed hub request → fan-out → collated HTTP response |
| `WS` | `/bind/{endpoint}` | Receive hub events; send `<datatype>-response` for requests |
| `GET` | `/api/hub/admin` | Admin dashboard HTML |
| `GET` | `/api/hub/admin/snapshot` | JSON dashboard (subscriptions, conferences, audit) |
| `WS` | `/ws/admin` | `admin.refresh` notifications (revision bump) |
| `POST` | `/oauth/authorize`, `/oauth/token` | Dev/mock OAuth for clients (disable with `HUB_MOCK_OAUTH=false`) |

### Authentication modes

| Mode | Hub settings | Client behavior |
|------|--------------|-----------------|
| **Local dev (default)** | `HUB_MOCK_OAUTH=true`, `HUB_REQUIRE_AUTH=false` | Clients `POST` hub `/oauth/*`; subscribe works without enforced Bearer checks |
| **Enforced mock tokens** | `HUB_REQUIRE_AUTH=true` | Subscribe/publish/request require Bearer JWT from mock `/oauth/token` |
| **Keycloak RS256** | `HUB_OIDC_ISSUER` + optional `HUB_REQUIRE_AUTH=true` | Hub validates Keycloak access tokens via JWKS; deploy IdP from [`cast-keycloak`](https://github.com/mbellehumeur/cast-keycloak) |

Bearer validation lives in [`Lib/oauth_tokens.py`](Lib/oauth_tokens.py) (HS256 mock + RS256/JWKS).

Conference UI: `/api/hub/conference-client`, APIs under `/api/hub/conference` and `/api/hub/conference-topics`.

Event names (`<datatype>-request` / `<datatype>-response`) are defined in [`hub_client.py`](hub_client.py) (top of file) and mirrored in vtk-js / VolView / OHIF.

---

## Subscribe and bind (prerequisite)

Before publish or request traffic, a client subscribes and opens its bind socket.

```mermaid
sequenceDiagram
    participant Client
    participant Hub as hub Hub
    participant BindWS as WS /bind/endpoint

    Client->>Hub: POST /api/hub/ hub.mode=subscribe
    Note over Client,Hub: hub.topic, hub.events, subscriber.name, channel=websocket
    Hub-->>Client: 202 hub.channel.endpoint ws(s)://host/bind/{uuid}
    Client->>BindWS: WebSocket connect
    BindWS-->>Client: connection.established
    Hub->>Hub: register_websocket(endpoint)
```

---

## Publish flow

A publisher sends a Hub notification to the hub. The hub matches **topic + event type** against each subscription, skips echo to the publisher (`subscriber.name` on the envelope), and delivers to WebSocket or WebSub subscribers. Conference participants may receive an extra copy when the message topic matches an active conference.

**Entry point:** `POST /api/hub/` or `POST /api/hub` (body contains `event` with `event.hub.topic`).

```mermaid
sequenceDiagram
    participant Publisher
    participant Hub as hub Hub
    participant SubA as Subscriber A WS
    participant SubB as Subscriber B WS
    participant WebSub as WebSub callback
    participant Admin as Admin clients

    Publisher->>Hub: POST /api/hub/
    Note over Publisher,Hub: JSON: id, event.hub.topic, event.hub.event, context

    Hub->>Hub: _handle_publish_notification
    Hub->>Hub: update last_context, audit received

    loop Each matching subscription
        alt channel = websocket and bound
            Hub->>SubA: send_text(notification JSON)
            Note over Hub,SubA: dicom-send: text frame + binary bytes
            Hub->>Hub: audit sent
        else channel = websub
            Hub->>WebSub: POST callback + X-Hub-Signature
        end
    end

    opt Message from conference participant
        Hub->>Hub: fan-out to other conference topics (no duplicate endpoint)
    end

    Hub->>Admin: admin.refresh (revision++)
    Hub-->>Publisher: {"status": "received"}
```

**Matching rules**

- Subscription `topic` must equal `event.hub.topic`, or subscription `topic` may be
  `*` to receive publishes for **any** non-empty `event.hub.topic`.
- Subscription `events` must include the event or `*`.
- Optional publish filters: `target.actor` (role) and `target.product.name` on the notification root
  (matches `client_info.productName` on the subscription).
- Publisher subscriber name is not delivered a copy of its own publish.

### `subscription-removed` (hub lifecycle)

When a subscription is removed (explicit unsubscribe, WebSocket disconnect, or send failure), the hub fans out a **`subscription-removed`** event to other subscribers on the same topic (same matching rules as publish). The removed peer does not receive the notification.

| Field | Description |
| --- | --- |
| `event.hub.event` | `subscription-removed` |
| `event.hub.topic` | Topic of the removed subscription |
| `subscriber.name` | Removed subscriber |
| `subscriber.product.name` | Optional product from subscription `client_info` |
| `event.context.reason` | `unsubscribe`, `websocket-disconnect`, or `send-failure` |
| `event.context.subscriber` | Same as `subscriber.name` |

Subscribe with `subscription-removed` in `hub.events` (or `*`) to receive these notifications.

**Binary file transfer:** binary batch only — `POST /api/hub/` as `multipart/related` (Hub JSON manifest + one part per `context.files[]` entry). Hub fans out one text WebSocket message with `files[].payloadId`; subscribers download via `GET /api/hub/payloads/{payloadId}` when the app calls `fetch_all_payloads`. Full write-up: Slicer extension `HubInterface/docs/binary-file-transfer.md` (same content as in a pw45 checkout). Legacy `multipart/form-data` returns HTTP 400.

**Filename policy:** When the hub stores binary bytes (HTTP payload store or multipart upload), `resource.fileName` must match an allowlisted suffix and pass a double-extension check. See [filename-policy.md](filename-policy.md). Disable with `HUB_FILENAME_POLICY=off`.

### DICOM send (`dicom-send`)

VolView publishes study/series/slice data as a single **binary batch** `dicom-send`: `context.files[]` manifest plus one `application/dicom` part per file (`multipart/related`). Receivers download bytes via `payloadId` when ready. Helpers: VolView `publishDicomSendSeries()` / `publishDicomSendForScope()` in [`hub-client.ts`](../../../src/io/hub-client.ts), `buildDicomSendManifest()` in [`build-dicom-send-manifest.ts`](../../../src/io/hub/build-dicom-send-manifest.ts).

---

## Hub request flow

A caller uses **HTTP** to ask the hub to dispatch a **typed** request to every matching subscriber. The request body includes top-level `id` and `timestamp` (like publish). Each target receives a WebSocket message with `hub.event = <datatype>-request` and `context.id` set to that correlation id. Targets reply on `/bind/{endpoint}` with `hub.event = <datatype>-response` and the same `context.id`. The hub waits (default 2s, `HUB_REQUEST_TIMEOUT_SECONDS`) and returns one collated JSON body.

**Entry point:** `POST /api/hub/request`

```mermaid
sequenceDiagram
    participant Requester
    participant Hub as hub Hub
    participant Target1 as Target subscriber 1
    participant Target2 as Target subscriber 2
    participant BindWS as WS /bind/{endpoint}

    Requester->>Hub: POST /api/hub/request
    Note over Requester,Hub: subscriber.name, event.hub.event, event.hub.topic?, subscriber.actor, target.product.name?

    Hub->>Hub: Match requester subscription (exists, connected)
    Hub->>Hub: Match targets by topic, actor, target.product.name
    Hub->>Hub: pending_requests[id] = expected set

    par Fan-out
        Hub->>Target1: WS <datatype>-request + context.id
        Hub->>Target2: WS <datatype>-request + context.id
    end

  par Responses on bind socket
        Target1->>BindWS: <datatype>-response (id in context)
        BindWS->>Hub: correlate to pending_requests
        Target2->>BindWS: <datatype>-response
        BindWS->>Hub: append response, drop from expected
    end

    Hub->>Hub: wait completion_event or timeout
    Hub->>Hub: pop pending_requests, audit, admin.refresh
    Hub-->>Requester: collated JSON (responses, missing, timedOut)
```

**Target matching**

| Field | Role |
|-------|------|
| `subscriber.name` | Requester identity (must have a subscription; should be WS-connected) |
| `event.hub.event` | Required `*-request` event name (for example `status-request`); hub dispatches this name as-is |
| `event.hub.topic` | Optional; all matches must use this hub topic |
| `event.context.dataType` | Optional handler token (for example `STATUS`, `PNGFULLSIZE`); copied into WS fan-out `context` |
| `subscriber.actor` | Source / requester role (included on forwarded WS events) |
| `target.actor` | Destination filter: subscription `actors` must include this keyword; omitted or `*` = all roles on topic. On POST request body, if `target.actor` is absent, `subscriber.actor` may be used as the filter. WS fan-out uses the same keys. |
| `productName` | Optional filter on `client_info.productName`; `*` means any |

**Event names:** clients send `event.hub.event` as the `*-request` name (build with `request_event_for` from a data-type token). Responses use the matching `*-response` name. Collated HTTP replies still include a top-level `dataType` derived from `event.context.dataType` or the request event name.

**Example request body:**

```json
{
  "subscriber.name": "VolView-ABC123",
  "event": {
    "hub.topic": "my-topic",
    "hub.event": "status-request",
    "context": { "dataType": "STATUS" }
  },
  "subscriber.actor": "WORKLIST_CLIENT",
  "target.actor": "WORKLIST_CLIENT"
}
```

**Collated HTTP response (success shape):**

```json
{
  "ok": true,
  "id": "<uuid>",
  "subscriber.name": "REQUESTER-NAME",
  "dataType": "JPGFULLSIZE",
  "subscriber.actor": "ID",
  "target.actor": "*",
  "responses": [
    {
      "id": "<envelope-id>",
      "subscriber": "VIEWER-ABC",
      "actor": "ID",
      "productName": "OHIF",
      "data": { }
    }
  ],
  "expected": ["VIEWER-ABC"],
  "missing": [],
  "timedOut": false
}
```

Each item in `responses` is one subscriber's `<datatype>-response` payload. The envelope `id` is the message id from the WebSocket event, not a separate `responseId` field.

`SCENEVIEW` uses the same WebSocket fan-out as other data types (`sceneview-request` / `sceneview-response`). Notes for implementing sceneview on the 3D Slicer side are in the HubInterface module's `sceneview-readme.md`.

---

## Admin dashboard

- **HTTP:** `GET /api/hub/admin/snapshot` — full state for the UI.
- **WebSocket:** `WS /ws/admin` — lightweight `admin.refresh` with `revision`; the page reloads snapshot when revision changes.
- **Reset:** `POST /api/admin/reset` — clears subscriptions, audit, conferences, auth codes; optional `single_user_mode`.

---

## Related files

| File | Role |
|------|------|
| [`hub.py`](hub.py) | Hub app, routes, `Hub` state |
| [`admin.html`](admin.html) | Admin UI |
| [`hub_client.py`](hub_client.py) | Per-`dataType` event-name helpers; `HubClient` ABC and `SlicerHubClient` |
| [`3dslicer-hub-ai-interface.py`](3dslicer-hub-ai-interface.py) | Standalone Hub subscriber (hub URLs from OHIF `hub.js`) |
| [`HUB-README.md`](HUB-README.md) | VolView server run/deploy guide |

For workspace-wide Hub protocol rules (event-name sync across Python/JS/TS, line endings), see [`../../AGENTS.md`](../../AGENTS.md).

---

## Python Hub client

**Library:** [`hub_client.py`](hub_client.py) defines `SlicerHubClient` — OAuth, subscribe, WebSocket bind, publish, and typed `request` / `send_hub_request_response`. It does not embed hub URLs; callers pass `HubConfig` and `SessionConfig`.

**Subscriber identity:** Clients generate `subscriber.name` locally as
`<productName>-<6 random A–Z0-9>` when the session is created (see
`generate_subscriber_name` in Python and `generateSubscriberName` in vtk-js).
Mock OAuth (`/oauth/authorize`, `/oauth/token`) returns tokens and **topic** only;
the hub registers the subscriber name from the subscribe form body.

**Standalone subscriber:** [`3dslicer-hub-ai-interface.py`](3dslicer-hub-ai-interface.py) connects to a remote hub over HTTP/WebSocket. Hub endpoints and OAuth credentials are hardcoded at the top of that file (copied from [`Viewers/platform/app/public/config/hub.js`](../../../../Viewers/platform/app/public/config/hub.js) `hub.hubs`). Edit `HUB_NAME` and `TOPIC` before running.

```bash
cd HubInterface/hub
python 3dslicer-hub-ai-interface.py
```

| `HUB_NAME` | Hub |
|------------|-----|
| `VOLVIEW-HUB` | `http://localhost:4014/api/hub` (default in interface script) |
| `VOLVIEW-HUB-CLOUD` | VolView Azure |
| `SLICER-HUB-CLOUD` | 3D Slicer Azure |
| `SLICER-HUB` | `http://localhost:2018/api/hub` |

No local hub process is required when using a cloud `HUB_NAME`.

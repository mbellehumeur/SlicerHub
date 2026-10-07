# STATUS request / response

`status-request` replaces `fhircastcontext-request` and `sceneview-request` for polling
connected Hub subscribers on a topic.

## Events

| Direction | Event | `context.dataType` |
|-----------|-------|-------------------|
| Request | `status-request` | `STATUS` |
| Response | `status-response` | `STATUS` |

Built via `request_event_for('STATUS')` / `requestEventFor('STATUS')` in client libraries.

## Request

HTTP `POST /api/hub/request` (or `client.request()` from a subscriber):

```json
{
  "subscriber.name": "VolView-ABC123",
  "subscriber.product.name": "VolView",
  "target.actor": "*",
  "event": {
    "hub.event": "status-request",
    "hub.topic": "USER-1",
    "context": { "dataType": "STATUS" }
  }
}
```

The hub fans out to every connected subscription on the topic whose `hub.events` includes
`status-request` (or `*`), matching `target.actor` / `target.product.name` filters.

## Response shapes (`responses[].data`)

### Worklist (`WORKLIST_CLIENT`)

Same body as the legacy fhircastcontext response:

```json
{
  "context.type": "ImagingStudy",
  "context": [ { "key": "study", "resource": { "resourceType": "ImagingStudy" } } ]
}
```

When no study is open:

```json
{ "context.type": "", "context": [] }
```

### Image display (VolView, OHIF, 3D Slicer ID)

```json
{
  "source": "status",
  "product": "VolView",
  "items": [{ "key": "availability", "value": "online" }],
  "sceneview": {
    "source": "sceneview",
    "product": "VolView",
    "window": { "screenX": 0, "screenY": 0, "outerWidth": 0, "outerHeight": 0, "innerWidth": 0, "innerHeight": 0 },
    "display": { "layoutName": null, "activeViewId": null, "layoutScreenRect": null, "layoutClientSize": null },
    "viewports": []
  }
}
```

`sceneview` matches the former standalone `sceneview-response` payload. Use
`data.sceneview` when rendering layout diagrams from a status poll.

`items[]` is extensible (`job`, `progress`, etc.) without changing worklist study loading.

### Image display — SlicerLive IRA

IRA answers STATUS with ImagingStudy context (when a study is open) and **always**
includes a `segmentation` context item (metadata only; voxels stay on the ID):

```json
{
  "context.type": "ImagingStudy",
  "context": [
    { "key": "study", "resource": { "resourceType": "ImagingStudy" } },
    {
      "key": "segmentation",
      "resource": {
        "segmentations": [
          {
            "sopInstanceUID": "1.2.3…",
            "seriesInstanceUID": "1.2.4…",
            "sourceSeriesInstanceUID": "1.2.5…",
            "fileName": "segmentations.dcm",
            "modality": "SEG",
            "segments": [
              {
                "number": 1,
                "label": "Liver",
                "color": [0.9, 0.2, 0.1],
                "terminology": {},
                "voxelCount": 12000,
                "volumeMm3": 48000.0
              }
            ]
          }
        ]
      }
    }
  ]
}
```

When no SEG is loaded, `segmentations` is `[]` — the `segmentation` key is still present.
Peers (e.g. reporting) poll with `status-request` and `target.actor: "ID"`.

### Other service providers

```json
{
  "source": "status",
  "product": "TOTALSEG",
  "items": [{ "key": "availability", "value": "online" }]
}
```

## Subscribe

Every app on a topic must list `status-request` in `hub.events` (or subscribe with `*`).

Remove `fhircastcontext-request` and `sceneview-request` from explicit event lists.

## Image display on connect

After WebSocket connect, image displays send `status-request` with `target.actor: *`, then:

1. Load study from the worklist response (`context.type === 'ImagingStudy'`).
2. Ignore other responders for study loading; their `sceneview` embed is for layout/status UI.

## `status-update` (one-way job log)

Service providers can publish progress lines to a **single** subscriber without a
request/response pair. VolView uses this for the Total Segmentator **Job Status** log.

| Field | Value |
|-------|--------|
| `hub.event` | `status-update` |
| `target.subscriber.name` | Destination subscriber (e.g. `VolView-ABC123` from the inbound send) |
| `event.context.message` | Human-readable line (required string) |
| `event.context.level` | `info` or `error` (optional) |

Do **not** set `target.actor` on subscriber-targeted publishes (e.g. do not inherit
a service provider's default `ID` actor). The hub filters by actor; worklist clients
use `WORKLIST_CLIENT` and would not receive `status-update` with `target.actor=ID`.
Omit `target.actor` or use `*` when `target.subscriber.name` is set.

Example publish envelope:

```json
{
  "subscriber.name": "TOTALSEG-XYZ789",
  "subscriber.product.name": "TOTALSEG",
  "target.subscriber.name": "VolView-ABC123",
  "event": {
    "hub.event": "status-update",
    "hub.topic": "USER-1",
    "context": {
      "message": "TotalSegmentator: starting segmentation topic=USER-1",
      "level": "info"
    }
  }
}
```

### Hub fan-out

When `target.subscriber.name` is set and not `*`, the hub delivers only to WebSocket /
WebSub subscriptions on the topic whose `subscriber` matches (case-insensitive). Omit
the field or use `*` for the usual fan-out to all matching subscribers (except publisher
echo).

Clients should list `status-update` in `hub.events` (or subscribe with `*`). This event
does not use the `<dataType>-request` / `-response` helpers.

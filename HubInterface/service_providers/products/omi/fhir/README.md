# SlicerHub OMI FHIR algorithm registry

Generated FHIR resources conforming to the [Open Medical Inference Protocol](https://simplifier.net/guide/OMI-Protocol-IG)
`OmiDevice` profile (`http://omi.de/fhir/registry/StructureDefinition/algorithm-device`).

## Regenerate

From `HubInterface/`:

```bash
node service_providers/products/omi/export_omi_fhir.mjs
```

Also writes the worklist slim catalog to:

`SlicerHub-js-clients/packages/worklist/omi-fhir-registry.json`

## Contents

| Path | Description |
|------|-------------|
| `Organization-SlicerHub.json` | Owning organization |
| `Device/*.json` | One OMI Device per algorithm |
| `StructureDefinition/*-input.json` / `*-output.json` | Lightweight I/O Parameter profile stubs |
| `registry-bundle.json` | Collection Bundle (Organization + all Devices) |

## Sources (38 algorithms)

- Evidence Creator products (`*.info.json`)
- MHub models (`mhub/MHubSkill/data/models_summary.json`)
- SAM-B (OHIF Cornerstone ONNX)

## Non-goals (this pass)

- No live FHIR registry server
- No `$register-service` / HealthcareService / Endpoint / heartbeat Observation
- StructureDefinitions are stubs, not full IG differential profiles

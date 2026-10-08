/**
 * Export OMI-profiled FHIR Device resources for all SlicerHub inference models.
 *
 * Sources:
 *   - product *.info.json (Evidence Creators)
 *   - MHub models_summary.json (30 models)
 *   - SAM-B stub (OHIF Cornerstone AI)
 *
 * Run from HubInterface/:
 *   node service_providers/products/omi/export_omi_fhir.mjs
 *
 * Writes:
 *   service_providers/products/omi/fhir/  (Devices, StructureDefinitions, Bundle, Organization)
 *   ../../SlicerHub-js-clients/packages/worklist/omi-fhir-registry.json  (slim catalog for UI)
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createHash } from "node:crypto";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const productsDir = path.resolve(__dirname, "..");
const fhirDir = path.join(__dirname, "fhir");
const deviceDir = path.join(fhirDir, "Device");
const sdDir = path.join(fhirDir, "StructureDefinition");
const summaryPath = path.join(
  productsDir,
  "mhub",
  "MHubSkill",
  "data",
  "models_summary.json"
);
const worklistCatalogPath = path.resolve(
  __dirname,
  "../../../../../SlicerHub-js-clients/packages/worklist/omi-fhir-registry.json"
);

const OMI_DEVICE_PROFILE =
  "http://omi.de/fhir/registry/StructureDefinition/algorithm-device";
const OMI_ORG_PROFILE =
  "http://omi.de/fhir/registry/StructureDefinition/omi-organization";
const BASE = "http://slicerhub.org/fhir";
const DEVICE_SID = `${BASE}/sid/device`;
const AI_SERVICE_CS =
  "http://omi.de/fhir/registry/CodeSystem/ai-service-type-codesystem";
const MATURITY_CS =
  "http://omi.de/fhir/registry/CodeSystem/maturity-level-codesystem";
const DCM = "http://dicom.nema.org/resources/ontology/DCM";
const SCT = "http://snomed.info/sct";

/** Products that actually run inference today. */
const REAL_PRODUCT_IDS = new Set(["totalseg", "dental", "flexray"]);

const PRODUCT_MODALITIES = {
  totalseg: ["CT", "MR"],
  dental: ["CT"],
  flexray: ["CR", "DX"],
  neuro: ["MR"],
  lung: ["CT"],
  txrv: ["CR", "DX"],
  mhub: ["CT", "MR", "PT", "CR", "SM"],
};

const PRODUCT_AI_TYPE = {
  totalseg: "segmentation",
  dental: "segmentation",
  flexray: "segmentation",
  neuro: "segmentation",
  lung: "diagnostics",
  txrv: "diagnostics",
  mhub: "segmentation",
};

const SAM = {
  id: "sam-b",
  modelName: "sam_b",
  label: "Segment Anything (SAM-B)",
  description:
    "Interactive marker-guided segmentation via SAM ViT-B ONNX encoder/decoder (OHIF Cornerstone AI).",
  modalities: ["CT", "MR", "CR", "DX", "US"],
  version: "sam_vit_b_01ec64-fp16",
  websiteUrl: "https://segment-anything.com/",
  githubUrl: "https://github.com/facebookresearch/segment-anything",
  encoderUrl:
    "https://huggingface.co/schmuell/sam-b-fp16/resolve/main/sam_vit_b_01ec64.encoder-fp16.onnx",
  decoderUrl:
    "https://huggingface.co/schmuell/sam-b-fp16/resolve/main/sam_vit_b_01ec64.decoder.onnx",
};

function ensureDirs() {
  for (const d of [fhirDir, deviceDir, sdDir]) {
    fs.mkdirSync(d, { recursive: true });
  }
}

function readJson(p) {
  return JSON.parse(fs.readFileSync(p, "utf8"));
}

function writeJson(p, obj) {
  fs.mkdirSync(path.dirname(p), { recursive: true });
  fs.writeFileSync(p, `${JSON.stringify(obj, null, 2)}\n`, "utf8");
}

function stableUuid(seed) {
  const h = createHash("sha256").update(String(seed)).digest();
  const bytes = Buffer.from(h.subarray(0, 16));
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = bytes.toString("hex");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20, 32)}`;
}

function findInfoJsonFiles(dir) {
  const out = [];
  for (const ent of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, ent.name);
    if (ent.isDirectory()) {
      if (ent.name === "omi" || ent.name === "MHubSkill" || ent.name === "__pycache__") {
        continue;
      }
      out.push(...findInfoJsonFiles(full));
    } else if (ent.name.endsWith(".info.json")) {
      out.push(full);
    }
  }
  return out;
}

function extractCiteUrl(cite, citeUrl) {
  if (citeUrl) return String(citeUrl).trim();
  const text = String(cite || "");
  const doi = text.match(/https?:\/\/(?:dx\.)?doi\.org\/[^\s)\]>",]+/i);
  if (doi) return doi[0].replace(/[.,;]+$/, "");
  const arxivAbs = text.match(/https?:\/\/arxiv\.org\/abs\/[^\s)\]>",]+/i);
  if (arxivAbs) return arxivAbs[0].replace(/[.,;]+$/, "");
  const arxivId = text.match(/arXiv[:\s]+(\d{4}\.\d{4,5})(?:v\d+)?/i);
  if (arxivId) return `https://arxiv.org/abs/${arxivId[1]}`;
  const bareDoi = text.match(/\b10\.\d{4,9}\/[^\s)\]>",]+/i);
  if (bareDoi) return `https://doi.org/${bareDoi[0].replace(/[.,;]+$/, "")}`;
  return "";
}

function modalityCoding(code) {
  const map = {
    CT: "Computed Tomography",
    MR: "Magnetic Resonance",
    MRI: "Magnetic Resonance",
    PT: "Positron emission tomography",
    CR: "Computed Radiography",
    DX: "Digital Radiography",
    SM: "Slide Microscopy",
    US: "Ultrasound",
    SEG: "Segmentation",
  };
  const c = String(code || "").toUpperCase() === "MRI" ? "MR" : String(code || "").toUpperCase();
  return {
    coding: [
      {
        system: DCM,
        code: c,
        display: map[c] || c,
      },
    ],
  };
}

function aiServiceTypeFromCategory(category) {
  const c = String(category || "").toLowerCase();
  if (c.includes("segment")) return "segmentation";
  if (c.includes("predict") || c.includes("classif") || c.includes("detect")) {
    return "diagnostics";
  }
  return "diagnostics";
}

function propertySlice(typeCode, typeDisplay, valueCode, valueDisplay, valueSystem) {
  return {
    type: {
      coding: [
        {
          system: SCT,
          code: typeCode,
          display: typeDisplay,
        },
      ],
    },
    valueCode: [
      {
        coding: [
          {
            system: valueSystem,
            code: valueCode,
            display: valueDisplay,
          },
        ],
      },
    ],
  };
}

function buildOrganization() {
  return {
    resourceType: "Organization",
    id: "SlicerHub",
    meta: {
      profile: [OMI_ORG_PROFILE],
    },
    identifier: [
      {
        system: `${BASE}/sid/organization`,
        value: "slicerhub",
      },
    ],
    active: true,
    name: "Slicer Hub",
    telecom: [
      {
        system: "url",
        value: "https://github.com/mbellehumeur/SlicerHub",
      },
    ],
  };
}

function buildParameterStructureDefinition(algoId, kind, title, description) {
  const id = `${algoId}-${kind}`;
  const url = `${BASE}/StructureDefinition/${id}`;
  const baseProfile =
    kind === "input"
      ? "http://omi.de/fhir/registry/StructureDefinition/omi-service-input-parameter"
      : "http://omi.de/fhir/registry/StructureDefinition/omi-service-output-parameter";
  return {
    resourceType: "StructureDefinition",
    id,
    url,
    name: id.replace(/[^A-Za-z0-9]/g, ""),
    title: `${title} ${kind} parameters`,
    status: "active",
    description:
      description ||
      `Lightweight OMI-style ${kind} Parameters profile stub for ${title}.`,
    fhirVersion: "4.0.1",
    kind: "resource",
    abstract: false,
    type: "Parameters",
    baseDefinition: baseProfile,
    derivation: "constraint",
  };
}

function buildDevice(algo) {
  const inputUrl = `${BASE}/StructureDefinition/${algo.id}-input`;
  const outputUrl = `${BASE}/StructureDefinition/${algo.id}-output`;
  const extensions = [
    {
      url: "http://omi.de/fhir/registry/StructureDefinition/input-parameter-extension",
      valueCanonical: inputUrl,
    },
    {
      url: "http://omi.de/fhir/registry/StructureDefinition/output-parameter-extension",
      valueCanonical: outputUrl,
    },
  ];

  if (algo.cite || algo.citeUrl) {
    const citeUrl = extractCiteUrl(algo.cite, algo.citeUrl);
    const ext = {
      url: "http://omi.de/fhir/registry/StructureDefinition/doi-extension",
      extension: [
        {
          url: "citation",
          valueString: String(algo.cite || algo.label || algo.id),
        },
      ],
    };
    if (citeUrl) {
      ext.extension.unshift({
        url: "doi",
        valueUri: citeUrl,
      });
    }
    extensions.push(ext);
  }

  if (algo.license) {
    extensions.push({
      url: "http://omi.de/fhir/registry/StructureDefinition/software-license-extension",
      extension: [
        {
          url: "license",
          valueString: String(algo.license),
        },
        ...(algo.licenseUrl
          ? [
              {
                url: "licenseUrl",
                valueUri: String(algo.licenseUrl),
              },
            ]
          : []),
      ],
    });
  }

  const properties = [];
  for (const mod of algo.modalities || []) {
    const m = modalityCoding(mod);
    properties.push(
      propertySlice(
        "360037004",
        "Imaging modality",
        m.coding[0].code,
        m.coding[0].display,
        DCM
      )
    );
  }

  const aiType = algo.aiServiceType || "diagnostics";
  properties.push(
    propertySlice(
      "410656007",
      "Type of service",
      aiType,
      aiType === "segmentation" ? "Segmentation" : "Diagnostics",
      AI_SERVICE_CS
    )
  );

  const maturity = algo.maturityLevel || "LVL1";
  properties.push(
    propertySlice(
      "246102003",
      "Maturity level",
      maturity,
      maturity.replace("LVL", "LEVEL "),
      MATURITY_CS
    )
  );

  const device = {
    resourceType: "Device",
    id: algo.id,
    meta: {
      profile: [OMI_DEVICE_PROFILE],
    },
    extension: extensions,
    identifier: [
      {
        system: DEVICE_SID,
        value: algo.uuid,
      },
    ],
    status: "active",
    deviceName: [
      {
        name: algo.label,
        type: "user-friendly-name",
      },
      {
        name: algo.modelName || algo.label,
        type: "model-name",
      },
    ],
    version: [
      {
        value: algo.version || "0.0.0",
      },
    ],
    property: properties,
    owner: {
      reference: "Organization/SlicerHub",
      display: "Slicer Hub",
    },
  };

  if (algo.description || algo.summary) {
    device.note = [
      {
        text: String(algo.description || algo.summary),
      },
    ];
  }

  if (algo.websiteUrl || algo.githubUrl) {
    device.note = device.note || [];
    const links = [algo.websiteUrl, algo.githubUrl].filter(Boolean).join(" · ");
    device.note.push({ text: `Links: ${links}` });
  }

  return device;
}

function productToAlgo(info) {
  const id = `product-${info.id}`;
  const real = REAL_PRODUCT_IDS.has(info.id);
  return {
    id,
    source: "product",
    productId: info.id,
    label: info.title || info.id,
    modelName: info.product || info.title || info.id,
    version: info.version || "0.1.0",
    description: info.summary || "",
    summary: info.summary || "",
    modalities: PRODUCT_MODALITIES[info.id] || ["OT"],
    aiServiceType: PRODUCT_AI_TYPE[info.id] || "diagnostics",
    maturityLevel: real ? "LVL3" : "LVL1",
    cite: info.cite,
    citeUrl: info.citeUrl,
    license: info.license,
    licenseUrl: info.licenseUrl,
    websiteUrl: info.websiteUrl,
    githubUrl: info.githubUrl,
    hubEvent: info.hubEvent,
    enabled: info.enabled !== false,
    uuid: stableUuid(`product:${info.id}`),
  };
}

function mhubToAlgo(name, model) {
  const modalities = (model.modalities || []).map((m) =>
    String(m).toUpperCase() === "MRI" ? "MR" : String(m).toUpperCase()
  );
  return {
    id: `mhub-${name}`,
    source: "mhub",
    mhubName: name,
    label: model.label || name,
    modelName: name,
    version: model.version || "0.1.0",
    description: model.description || "",
    summary: model.description || "",
    modalities: modalities.length ? modalities : ["CT"],
    aiServiceType: aiServiceTypeFromCategory(model.category),
    maturityLevel: "LVL2",
    cite: model.cite,
    citeUrl: extractCiteUrl(model.cite, ""),
    license: model.license_code || model.license_weights,
    websiteUrl: `https://mhub.ai/models/${encodeURIComponent(name)}`,
    githubUrl: undefined,
    dockerImage: model.docker_image,
    category: model.category,
    segmentCount: model.segment_count,
    inputs: model.inputs,
    uuid: stableUuid(`mhub:${name}`),
  };
}

function samToAlgo() {
  return {
    id: "sam-b",
    source: "sam",
    label: SAM.label,
    modelName: SAM.modelName,
    version: SAM.version,
    description: SAM.description,
    summary: SAM.description,
    modalities: SAM.modalities,
    aiServiceType: "segmentation",
    maturityLevel: "LVL3",
    websiteUrl: SAM.websiteUrl,
    githubUrl: SAM.githubUrl,
    encoderUrl: SAM.encoderUrl,
    decoderUrl: SAM.decoderUrl,
    uuid: stableUuid("sam:sam_b"),
  };
}

function slimEntry(algo, device) {
  return {
    id: algo.id,
    source: algo.source,
    label: algo.label,
    modelName: algo.modelName,
    version: algo.version || "0.0.0",
    modalities: algo.modalities || [],
    aiServiceType: algo.aiServiceType,
    maturityLevel: algo.maturityLevel,
    description: algo.description || algo.summary || "",
    websiteUrl: algo.websiteUrl || "",
    githubUrl: algo.githubUrl || "",
    inputProfile: `${BASE}/StructureDefinition/${algo.id}-input`,
    outputProfile: `${BASE}/StructureDefinition/${algo.id}-output`,
    device,
  };
}

function main() {
  ensureDirs();

  const org = buildOrganization();
  writeJson(path.join(fhirDir, "Organization-SlicerHub.json"), org);

  const algos = [];

  for (const infoPath of findInfoJsonFiles(productsDir)) {
    const info = readJson(infoPath);
    algos.push(productToAlgo(info));
  }

  const summary = readJson(summaryPath);
  for (const [name, model] of Object.entries(summary.models || {})) {
    algos.push(mhubToAlgo(name, model));
  }

  algos.push(samToAlgo());

  algos.sort((a, b) => a.id.localeCompare(b.id));

  const devices = [];
  const structureDefs = [];
  const slimAlgorithms = [];

  for (const algo of algos) {
    const device = buildDevice(algo);
    devices.push(device);
    writeJson(path.join(deviceDir, `${algo.id}.json`), device);

    const inSd = buildParameterStructureDefinition(
      algo.id,
      "input",
      algo.label,
      `Input contract for ${algo.label} (${algo.source}).`
    );
    const outSd = buildParameterStructureDefinition(
      algo.id,
      "output",
      algo.label,
      `Output contract for ${algo.label} (${algo.source}).`
    );
    structureDefs.push(inSd, outSd);
    writeJson(path.join(sdDir, `${algo.id}-input.json`), inSd);
    writeJson(path.join(sdDir, `${algo.id}-output.json`), outSd);

    slimAlgorithms.push(slimEntry(algo, device));
  }

  const bundle = {
    resourceType: "Bundle",
    id: "slicerhub-omi-algorithm-registry",
    meta: {
      lastUpdated: new Date().toISOString(),
    },
    type: "collection",
    timestamp: new Date().toISOString(),
    total: 1 + devices.length,
    entry: [
      {
        fullUrl: `${BASE}/Organization/SlicerHub`,
        resource: org,
      },
      ...devices.map((d) => ({
        fullUrl: `${BASE}/Device/${d.id}`,
        resource: d,
      })),
    ],
  };
  writeJson(path.join(fhirDir, "registry-bundle.json"), bundle);

  const slim = {
    generated: new Date().toISOString(),
    generator: "export_omi_fhir.mjs",
    omiDeviceProfile: OMI_DEVICE_PROFILE,
    organization: org,
    algorithmCount: slimAlgorithms.length,
    algorithms: slimAlgorithms,
  };
  writeJson(worklistCatalogPath, slim);

  const readme = `# SlicerHub OMI FHIR algorithm registry

Generated FHIR resources conforming to the [Open Medical Inference Protocol](https://simplifier.net/guide/OMI-Protocol-IG)
\`OmiDevice\` profile (\`${OMI_DEVICE_PROFILE}\`).

## Regenerate

From \`HubInterface/\`:

\`\`\`bash
node service_providers/products/omi/export_omi_fhir.mjs
\`\`\`

Also writes the worklist slim catalog to:

\`SlicerHub-js-clients/packages/worklist/omi-fhir-registry.json\`

## Contents

| Path | Description |
|------|-------------|
| \`Organization-SlicerHub.json\` | Owning organization |
| \`Device/*.json\` | One OMI Device per algorithm |
| \`StructureDefinition/*-input.json\` / \`*-output.json\` | Lightweight I/O Parameter profile stubs |
| \`registry-bundle.json\` | Collection Bundle (Organization + all Devices) |

## Sources (${slimAlgorithms.length} algorithms)

- Evidence Creator products (\`*.info.json\`)
- MHub models (\`mhub/MHubSkill/data/models_summary.json\`)
- SAM-B (OHIF Cornerstone ONNX)

## Non-goals (this pass)

- No live FHIR registry server
- No \`$register-service\` / HealthcareService / Endpoint / heartbeat Observation
- StructureDefinitions are stubs, not full IG differential profiles
`;

  fs.writeFileSync(path.join(fhirDir, "README.md"), readme, "utf8");

  console.log(
    `Wrote ${devices.length} Devices, ${structureDefs.length} StructureDefinitions`
  );
  console.log(`Bundle: ${path.join(fhirDir, "registry-bundle.json")}`);
  console.log(`Worklist catalog: ${worklistCatalogPath}`);
}

main();

/**
 * Export slim IRA MHub catalog (mhub-models.ts) + SegDB lookup TS from the
 * vendored MHubSkill caches, enriched with per-model GitHub / website / cite.
 *
 * Run from SlicerHub HubInterface/:
 *   node resource_servers/products/mhub/export_ira_catalog.mjs
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const summaryPath = path.join(__dirname, "MHubSkill", "data", "models_summary.json");
const segdbPath = path.join(__dirname, "MHubSkill", "data", "segdb_cache.json");
const pw46Root = path.resolve(__dirname, "../../../../../../pw46");
const outMhubPath = path.join(pw46Root, "SlicerLive/render/demos/ira/mhub-models.ts");
const outSegdbPath = path.join(pw46Root, "reporting/segdb-lookup.ts");

function canonBodyPart(raw) {
  const s = String(raw || "").trim();
  if (!s) return "";
  const u = s.toUpperCase().replace(/[\s_-]+/g, "");
  const map = {
    WHOLEBODY: "WHOLEBODY",
    CHEST: "Chest",
    ABDOMEN: "Abdomen",
    PROSTATE: "Prostate",
    KIDNEY: "Kidney",
    LIVER: "Liver",
    LUNG: "Lung",
    SPINE: "Spine",
    BREAST: "Breast",
  };
  return map[u] || s.charAt(0).toUpperCase() + s.slice(1).toLowerCase();
}

function extractCiteUrl(cite) {
  const text = String(cite || "");
  const doi = text.match(
    /https?:\/\/(?:dx\.)?doi\.org\/[^\s)\]>",]+/i
  );
  if (doi) return doi[0].replace(/[.,;]+$/, "");
  const arxivAbs = text.match(/https?:\/\/arxiv\.org\/abs\/[^\s)\]>",]+/i);
  if (arxivAbs) return arxivAbs[0].replace(/[.,;]+$/, "");
  const arxivId = text.match(/arXiv[:\s]+(\d{4}\.\d{4,5})(?:v\d+)?/i);
  if (arxivId) return `https://arxiv.org/abs/${arxivId[1]}`;
  const bareDoi = text.match(/\b10\.\d{4,9}\/[^\s)\]>",]+/i);
  if (bareDoi) return `https://doi.org/${bareDoi[0].replace(/[.,;]+$/, "")}`;
  return "";
}

async function fetchMeta(name) {
  const url = `https://raw.githubusercontent.com/MHubAI/models/main/models/${encodeURIComponent(name)}/meta.json`;
  try {
    const res = await fetch(url);
    if (!res.ok) {
      console.warn(`meta miss ${name}: HTTP ${res.status}`);
      return null;
    }
    return await res.json();
  } catch (err) {
    console.warn(`meta miss ${name}:`, err instanceof Error ? err.message : err);
    return null;
  }
}

function readJsonAllowingNan(filePath) {
  // SegDB Python dumps sometimes emit bare NaN (invalid JSON).
  const raw = fs
    .readFileSync(filePath, "utf8")
    .replace(/:\s*NaN\b/g, ": null");
  return JSON.parse(raw);
}

const summary = JSON.parse(fs.readFileSync(summaryPath, "utf8"));
const segdb = readJsonAllowingNan(segdbPath);
const entries = Object.entries(summary.models || {});
entries.sort((a, b) =>
  String(a[1].label || a[0]).localeCompare(String(b[1].label || b[0]))
);

const models = [];
for (const [name, info] of entries) {
  const bodyParts = [
    ...new Set(
      (Array.isArray(info.inputs) ? info.inputs : [])
        .map((inp) => canonBodyPart(inp?.bodypartexamined))
        .filter(Boolean)
    ),
  ].sort((a, b) => a.localeCompare(b));

  const segments = Array.isArray(info.segments)
    ? info.segments.map(String).filter(Boolean)
    : [];

  const cite = String(info.cite || "").trim();
  const websiteUrl = `https://mhub.ai/models/${name}`;
  let githubUrl = "";
  let citeUrl = extractCiteUrl(cite);

  const meta = await fetchMeta(name);
  if (meta && typeof meta === "object") {
    const details = meta.details && typeof meta.details === "object" ? meta.details : {};
    const gh = String(details.github || "").trim();
    if (gh) githubUrl = gh;
    if (!citeUrl && Array.isArray(details.publications)) {
      for (const pub of details.publications) {
        const uri = String(pub?.uri || "").trim();
        if (uri) {
          citeUrl = uri;
          break;
        }
      }
    }
  }

  const def = {
    name,
    label: String(info.label || name),
    description: String(info.description || ""),
    modalities: Array.isArray(info.modalities) ? info.modalities.map(String) : [],
    category: String(info.category || ""),
    bodyParts,
    segments,
    websiteUrl,
  };
  if (githubUrl) def.githubUrl = githubUrl;
  if (cite) def.cite = cite;
  if (citeUrl) def.citeUrl = citeUrl;
  models.push(def);
  console.log(
    `${name}: segs=${segments.length} github=${githubUrl ? "yes" : "no"} citeUrl=${citeUrl ? "yes" : "no"}`
  );
}

const segdbEntries = {};
for (const [id, info] of Object.entries(segdb.segments || {})) {
  segdbEntries[id] = {
    name: String(info.name || id),
    categoryCode: String(info.category_code ?? "123037004"),
    categoryScheme: String(info.category_scheme || "SCT"),
    categoryMeaning: String(info.category_meaning || "Body structure"),
    typeCode: String(info.type_code ?? ""),
    typeScheme: String(info.type_scheme || "SCT"),
    typeMeaning: String(info.type_meaning || info.name || id),
    modifier:
      info.modifier != null && info.modifier !== ""
        ? String(info.modifier)
        : "",
  };
}

const mhubHeader = `/**
 * Slim MHub model catalog for IRA Medical Inference / worklist.
 * Source: products/mhub/MHubSkill/data/models_summary.json
 * (cache_date: ${summary.cache_date || "unknown"}).
 * Enriched with GitHub / website / cite via:
 *   node resource_servers/products/mhub/export_ira_catalog.mjs
 */

export type MhubModelDef = {
  name: string;
  label: string;
  description: string;
  modalities: string[];
  category: string;
  /** Canonical body parts from model inputs[].bodypartexamined */
  bodyParts: string[];
  /** SegDB segment IDs this model can output (skill find). */
  segments: string[];
  githubUrl?: string;
  websiteUrl?: string;
  cite?: string;
  citeUrl?: string;
};

/** @type {readonly MhubModelDef[]} */
export const MHUB_MODELS: readonly MhubModelDef[] = Object.freeze(${JSON.stringify(models, null, 2)});

function modalityTokens(entry: string): string[] {
  return String(entry || "")
    .split("|")
    .map((t) => t.trim().toUpperCase())
    .filter(Boolean)
    .map((t) => (t === "MRI" ? "MR" : t === "PET" ? "PT" : t));
}

function modelMatchesModality(model: MhubModelDef, modality: string): boolean {
  const want = modality.trim().toUpperCase();
  if (!want) return true;
  const normalizedWant = want === "MRI" ? "MR" : want === "PET" ? "PT" : want;
  return model.modalities.some((m) => modalityTokens(m).includes(normalizedWant));
}

/** Filter skill models by open-study modality. Null/empty → all models. */
export function listMhubModelsForModality(
  modality: string | null | undefined,
): MhubModelDef[] {
  const raw = String(modality || "").trim();
  if (!raw) return [...MHUB_MODELS];
  return MHUB_MODELS.filter((m) => modelMatchesModality(m, raw));
}

function canonBodyPartKey(raw: string): string {
  return String(raw || "")
    .trim()
    .toUpperCase()
    .replace(/[\\s_-]+/g, "");
}

/** Unique body-part options for a model list (sorted, WHOLEBODY last). */
export function listMhubBodyPartsForModels(
  models: readonly MhubModelDef[],
): string[] {
  const parts = new Set<string>();
  for (const model of models) {
    for (const bp of model.bodyParts || []) {
      const t = String(bp || "").trim();
      if (t) parts.add(t);
    }
  }
  return [...parts].sort((a, b) => {
    if (a === "WHOLEBODY") return 1;
    if (b === "WHOLEBODY") return -1;
    return a.localeCompare(b);
  });
}

/** Filter models that declare the given body part examined. */
export function listMhubModelsForBodyPart(
  models: readonly MhubModelDef[],
  bodyPart: string | null | undefined,
): MhubModelDef[] {
  const want = String(bodyPart || "").trim();
  if (!want) return [...models];
  const wantKey = canonBodyPartKey(want);
  return models.filter((m) =>
    (m.bodyParts || []).some((bp) => canonBodyPartKey(bp) === wantKey),
  );
}

/** Normalize anatomy search terms like mhub_helper.find_models_for_anatomy. */
export function normalizeMhubAnatomyTerms(
  query: string | null | undefined,
): string[] {
  return String(query || "")
    .trim()
    .split(/[\\s,;+/]+/)
    .map((t) => t.trim().toUpperCase().replace(/[\\s-]+/g, "_"))
    .filter(Boolean);
}

/**
 * Find models whose segment IDs contain any anatomy term (skill \`find\`).
 * Sorted by number of matched segments (descending).
 */
export function findMhubModelsForAnatomy(
  query: string | null | undefined,
  models: readonly MhubModelDef[] = MHUB_MODELS,
): MhubModelDef[] {
  const terms = normalizeMhubAnatomyTerms(query);
  if (!terms.length) return [...models];
  const scored: { model: MhubModelDef; hits: number }[] = [];
  for (const model of models) {
    const segments = (model.segments || []).map((s) => String(s).toUpperCase());
    if (!segments.length) continue;
    let hits = 0;
    for (const seg of segments) {
      if (terms.some((t) => seg.includes(t))) hits += 1;
    }
    if (hits > 0) scored.push({ model, hits });
  }
  scored.sort((a, b) => b.hits - a.hits || a.model.label.localeCompare(b.model.label));
  return scored.map((s) => s.model);
}

/** Known body-part tokens for inferring from study description / series text. */
const BODY_PART_HINTS: { key: string; label: string; patterns: RegExp[] }[] = [
  { key: "KIDNEY", label: "Kidney", patterns: [/\\bkidn(?:ey|eys)?\\b/i, /\\brenal\\b/i] },
  { key: "LIVER", label: "Liver", patterns: [/\\bliver\\b/i, /\\bhepatic\\b/i] },
  { key: "LUNG", label: "Lung", patterns: [/\\blung(?:s)?\\b/i, /\\bpulmon/i] },
  { key: "CHEST", label: "Chest", patterns: [/\\bchest\\b/i, /\\bthorax\\b/i, /\\bthoracic\\b/i] },
  { key: "ABDOMEN", label: "Abdomen", patterns: [/\\babdomen\\b/i, /\\babdominal\\b/i] },
  { key: "PROSTATE", label: "Prostate", patterns: [/\\bprostate\\b/i] },
  { key: "BREAST", label: "Breast", patterns: [/\\bbreast\\b/i, /\\bmammo/i] },
  { key: "SPINE", label: "Spine", patterns: [/\\bspine\\b/i, /\\bspinal\\b/i, /\\bvertebra/i] },
  { key: "WHOLEBODY", label: "WHOLEBODY", patterns: [/\\bwhole\\s*body\\b/i, /\\bwb\\b/i] },
];

/**
 * Infer a canonical body-part label from free text (study description, etc.).
 * Returns "" when no confident match.
 */
export function inferMhubBodyPartFromText(
  text: string | null | undefined,
): string {
  const raw = String(text || "").trim();
  if (!raw) return "";
  for (const hint of BODY_PART_HINTS) {
    if (hint.patterns.some((re) => re.test(raw))) return hint.label;
  }
  return "";
}

/** Extract ImagingStudy description + modality coding from FHIRcast context. */
export function mhubContextHintsFromImagingStudy(
  context: unknown[] | null | undefined,
): { modality: string; description: string; bodyPart: string } {
  let modality = "";
  let description = "";
  if (!Array.isArray(context)) {
    return { modality, description, bodyPart: "" };
  }

  const normalizeMod = (raw: string): string => {
    let m = String(raw || "").trim().toUpperCase();
    if (m === "MRI") m = "MR";
    if (m === "PET") m = "PT";
    if (m === "CT" || m === "CTAC") return "CT";
    if (m === "MR" || m === "PT" || m === "NM") return m === "NM" ? "PT" : m;
    return "";
  };

  const modalityFromCoding = (value: unknown): string => {
    if (!value) return "";
    if (typeof value === "string") return normalizeMod(value);
    if (Array.isArray(value)) {
      for (const entry of value) {
        const hit = modalityFromCoding(entry);
        if (hit) return hit;
      }
      return "";
    }
    if (typeof value === "object") {
      const obj = value as { code?: unknown; coding?: unknown; text?: unknown };
      if (obj.coding != null) return modalityFromCoding(obj.coding);
      if (obj.code != null) return normalizeMod(String(obj.code));
      if (obj.text != null) return normalizeMod(String(obj.text));
    }
    return "";
  };

  for (const item of context) {
    if (!item || typeof item !== "object") continue;
    const key = String((item as { key?: unknown }).key || "");
    const resource = (item as { resource?: unknown }).resource;
    if (!resource || typeof resource !== "object") continue;
    const res = resource as {
      modality?: unknown;
      description?: unknown;
      series?: unknown;
      files?: unknown;
    };
    if (!description && res.description != null) {
      description = String(res.description).trim();
    }
    if (!modality && res.modality != null) {
      modality = modalityFromCoding(res.modality);
    }
    if (!modality && Array.isArray(res.series)) {
      for (const series of res.series) {
        if (!series || typeof series !== "object") continue;
        modality = modalityFromCoding(
          (series as { modality?: unknown }).modality,
        );
        if (modality) break;
      }
    }
    // IDC builders put modality on volume file labels (role: volume), not study.modality.
    if (!modality && (key === "files" || Array.isArray(res.files))) {
      const files = Array.isArray(res.files) ? res.files : [];
      for (const file of files) {
        if (!file || typeof file !== "object") continue;
        const row = file as { role?: unknown; label?: unknown };
        const role = String(row.role || "").trim().toLowerCase();
        if (role && role !== "volume") continue;
        modality = normalizeMod(String(row.label || ""));
        if (modality) break;
      }
    }
  }
  const bodyPart = inferMhubBodyPartFromText(description);
  return { modality, description, bodyPart };
}
`;

const segdbHeader = `/**
 * Slim SegDB → SNOMED lookup for DICOM SR reporting (and IRA catalog fill).
 * Source: products/mhub/MHubSkill/data/segdb_cache.json
 * (cache_date: ${segdb.cache_date || "unknown"}).
 * Generated by:
 *   node resource_servers/products/mhub/export_ira_catalog.mjs
 */

export type SegdbEntry = {
  name: string;
  categoryCode: string;
  categoryScheme: string;
  categoryMeaning: string;
  typeCode: string;
  typeScheme: string;
  typeMeaning: string;
  modifier: string;
};

export type SegdbCoded = {
  scheme: string;
  value: string;
  meaning: string;
};

export type SegdbTerminology = {
  category: SegdbCoded | null;
  type: SegdbCoded | null;
  typeModifier: SegdbCoded | null;
  region: SegdbCoded | null;
};

/** @type {Readonly<Record<string, SegdbEntry>>} */
export const SEGDB_BY_ID: Readonly<Record<string, SegdbEntry>> = Object.freeze(
  ${JSON.stringify(segdbEntries, null, 2)}
);

function normalizeSegKey(raw: string): string {
  return String(raw || "")
    .trim()
    .toUpperCase()
    .replace(/[^A-Z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "");
}

function normalizeNameKey(raw: string): string {
  return String(raw || "")
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, " ")
    .replace(/\\s+/g, " ")
    .trim();
}

const SEGDB_BY_NAME: ReadonlyMap<string, string> = (() => {
  const map = new Map<string, string>();
  for (const [id, entry] of Object.entries(SEGDB_BY_ID)) {
    const nameKey = normalizeNameKey(entry.name);
    if (nameKey && !map.has(nameKey)) map.set(nameKey, id);
    const idAsName = normalizeNameKey(id.replace(/_/g, " "));
    if (idAsName && !map.has(idAsName)) map.set(idAsName, id);
  }
  return map;
})();

/** Resolve a SegDB id from segment label or SegDB id string. */
export function resolveSegdbId(labelOrId: string | null | undefined): string | null {
  const raw = String(labelOrId || "").trim();
  if (!raw) return null;
  const asId = normalizeSegKey(raw);
  if (asId && SEGDB_BY_ID[asId]) return asId;
  const byName = SEGDB_BY_NAME.get(normalizeNameKey(raw));
  return byName || null;
}

function coded(
  scheme: string,
  value: string,
  meaning: string,
): SegdbCoded | null {
  const v = String(value || "").trim();
  if (!v) return null;
  return {
    scheme: String(scheme || "SCT"),
    value: v,
    meaning: String(meaning || "").trim(),
  };
}

/** Build catalog-style terminology from a SegDB entry. */
export function terminologyFromSegdbEntry(entry: SegdbEntry): SegdbTerminology {
  return {
    category: coded(
      entry.categoryScheme,
      entry.categoryCode,
      entry.categoryMeaning,
    ),
    type: coded(entry.typeScheme, entry.typeCode, entry.typeMeaning),
    typeModifier: null,
    region: null,
  };
}

/**
 * Look up SegDB terminology for a segment label / id.
 * Returns null when no match.
 */
export function lookupSegdbTerminology(
  labelOrId: string | null | undefined,
): SegdbTerminology | null {
  const id = resolveSegdbId(labelOrId);
  if (!id) return null;
  const entry = SEGDB_BY_ID[id];
  if (!entry) return null;
  return terminologyFromSegdbEntry(entry);
}

/**
 * Fill missing type/category codes from SegDB; never overrides existing codes.
 */
export function enrichTerminologyFromSegdb<T extends {
  category: SegdbCoded | null;
  type: SegdbCoded | null;
  typeModifier: SegdbCoded | null;
  region: SegdbCoded | null;
}>(
  label: string | null | undefined,
  terminology: T | null | undefined,
): T | undefined {
  const hasType = Boolean(terminology?.type?.value);
  const hasCategory = Boolean(terminology?.category?.value);
  if (hasType && hasCategory) return terminology || undefined;
  const fromDb = lookupSegdbTerminology(label);
  if (!fromDb) return terminology || undefined;
  return {
    category: hasCategory ? terminology!.category : fromDb.category,
    type: hasType ? terminology!.type : fromDb.type,
    typeModifier: terminology?.typeModifier ?? fromDb.typeModifier,
    region: terminology?.region ?? fromDb.region,
  } as T;
}
`;

fs.mkdirSync(path.dirname(outMhubPath), { recursive: true });
fs.writeFileSync(outMhubPath, mhubHeader);
console.log(`Wrote ${models.length} models → ${outMhubPath}`);

fs.mkdirSync(path.dirname(outSegdbPath), { recursive: true });
fs.writeFileSync(outSegdbPath, segdbHeader);
console.log(
  `Wrote ${Object.keys(segdbEntries).length} SegDB entries → ${outSegdbPath}`
);

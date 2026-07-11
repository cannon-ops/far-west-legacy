# Scanned-Obituary Pipeline (Milestone 3) — DRAFT

**Status:** DESIGN DRAFT — for Joel's review. No code exists for any of this.
**Scope:** the path from a scanned obituary image (newspaper clipping, full page, or PDF)
to reviewed, structured genealogical data with provenance and confidence — feeding the same
review + FamilySearch-upload path as pasted text.
**Author:** A13 (Claude Code, Fable), 2026-07-10 overnight design session.
**Companion doc:** `docs/familysearch-integration-DRAFT.md` (the downstream write path).
**Builds on:** `planning/familysearch-upload-plan.md` (FWL-006), `ARCHITECTURE.md`,
`docs/data_schema.md`, `prompts/obituary_extract.md`.

---

## 0. Why this milestone exists

The working pipeline (Milestone 1) handles *text*: paste or URL → Haiku extraction → review
UI → approved JSON. The people who most want FWL are holding *paper*:

- **Matthew Johnson / Perfection Image** — an archivist team that scans documents for a
  living. Their raw material is TIFF/JPEG/PDF scans, often batches of hundreds.
- **Daviess County Historical Society** — file drawers of newspaper clippings.
- **Mid-West Genealogy Center patrons** — a photo of a clipping taken on a phone.

One correction to the session brief: FWL has **no GEDCOM output today**. The durable output
is the extraction JSON (`docs/data_schema.md`); GEDCOM X is the *wire format* of the planned
FamilySearch upload (FWL-006 §2). This doc treats structured JSON as the pipeline's product,
with GEDCOM X mapping downstream and an optional GEDCOM 7 file export as a designed-but-
deferred feature (§7.3).

---

## 1. Design principles

1. **Don't fork the pipeline — extend its front.** A scan becomes *text*, then rides the
   existing, tested text path (extract → review → approve → upload). One extraction prompt,
   one schema, one review UI.
2. **Transcription and extraction are separate stages.** A one-shot "image → JSON" call is
   tempting but conflates two failure modes (misread the ink vs. misinterpreted the
   sentence), destroys the reviewable intermediate artifact, and makes confidence
   unattributable. Two stages: *transcribe* (image → verbatim text), then *extract*
   (text → JSON, the existing path).
3. **The human gate stays absolute.** Confidence scores prioritize the reviewer's attention;
   they never authorize skipping review. Same philosophy as FWL-006 §3 (no unattended
   duplicate resolution).
4. **Provenance is a chain, not a field.** Every artifact records what produced it:
   scan → transcript → extraction → human edits → FamilySearch writes. Any fact in the tree
   must be traceable back to pixels.
5. **Design for one scan; leave doors open for a thousand.** The archivist batch workflow is
   real but later (§9, M3.4). Nothing in the single-scan design may preclude it — e.g., job
   state must not assume an interactive browser session started the job.

---

## 2. Pipeline architecture

```
┌────────────────────────────────────────────────────────────────┐
│ 1. INGEST                                                      │
│   Upload: JPEG / PNG / TIFF / PDF (phone photo, flatbed scan)  │
│   → normalize (§3): format conversion, resize, page split      │
│   → content hash (sha256) + capture-metadata form (§8.1)       │
│   → job dir: tmp/<job_id>/scan.jpg + manifest.json             │
└──────────────────────────────┬─────────────────────────────────┘
                               ▼
┌────────────────────────────────────────────────────────────────┐
│ 2. SEGMENT (only when needed, §4)                              │
│   Claude vision: "how many distinct obituaries on this image?" │
│   1 obituary  → pass through                                   │
│   N obituaries → N child jobs, each with a region crop         │
└──────────────────────────────┬─────────────────────────────────┘
                               ▼
┌────────────────────────────────────────────────────────────────┐
│ 3. TRANSCRIBE (§5)                                             │
│   Claude vision (Sonnet default; Haiku evaluated in M3.0)      │
│   image → verbatim text + [illegible] markers + layout notes   │
│   + portrait-photo detection (present / absent + region)       │
│   → tmp/<job_id>/transcript.json                               │
└──────────────────────────────┬─────────────────────────────────┘
                               ▼ plain text
┌────────────────────────────────────────────────────────────────┐
│ 4. EXTRACT — existing src/extract.py, unchanged path           │
│   Haiku + prompts/obituary_extract.md → schema JSON            │
│   + extraction meta (§6): per-field confidence + evidence      │
└──────────────────────────────┬─────────────────────────────────┘
                               ▼
┌────────────────────────────────────────────────────────────────┐
│ 5. REVIEW — existing Flask UI, extended                        │
│   3-pane: scan image | transcript | editable fields            │
│   low-confidence fields highlighted; edits recorded (§8.1)     │
└──────────────────────────────┬─────────────────────────────────┘
                               ▼ approved JSON + provenance manifest
┌────────────────────────────────────────────────────────────────┐
│ 6. OUTPUT                                                      │
│   output/<Surname_Given>.json  (schema v2, §7)                 │
│   + provenance manifest (§8)                                   │
│   → FamilySearch upload path (FWL-006): scan image becomes a   │
│     Memories document; Source Description points at it (§8.2)  │
│   → optional GEDCOM 7 export (deferred, §7.3)                  │
└────────────────────────────────────────────────────────────────┘
```

New modules, mirroring the existing one-module-per-stage style:

| Module | Responsibility | Network? |
| --- | --- | --- |
| `src/ingest.py` | Validate/normalize uploads, page-split PDFs, hashing, job dirs | No |
| `src/transcribe.py` | Claude vision calls: segmentation probe + transcription | Anthropic only |
| `src/extract.py` | *(existing — gains optional confidence/evidence output, §6)* | Anthropic only |
| `src/provenance.py` | Manifest creation/append; pure | No |

`src/app.py` gains an upload route and the 3-pane review variant. No new framework, no queue
system, no database — files in `tmp/<job_id>/`, same as today (durable-storage caveat in §9).

---

## 3. Ingest & normalization (M3.1)

**Accepted inputs:** JPEG, PNG, TIFF, single- and multi-page PDF. Phone photos are first-class
(that's the library-patron case) — expect rotation, keystone skew, uneven lighting.

**Normalization is deliberately minimal.** Claude vision is robust to moderate skew/lighting;
classic OCR-era preprocessing (binarization, deskew) is not obviously needed and adds
dependencies. v1 does only what the API requires:

- Convert TIFF → PNG (Anthropic API accepts JPEG/PNG/GIF/WebP, not TIFF).
- Downscale so the long edge is ≤ ~1568 px *for the segmentation probe* (cheap), but send the
  transcription call a higher-resolution crop — newsprint body text at 6–8 pt needs pixels.
  Resize policy is an M3.0 measurement, not a guess: transcribe the same fixture at several
  resolutions and find the accuracy/cost knee.
- Respect API hard limits (≈5 MB and 8000×8000 px per image) with re-encode/tiling as needed.
- Multi-page PDF → one image per page → each page enters segmentation independently.
  (Anthropic's native PDF input is an alternative; per-page images keep segmentation and
  cropping in our control. Decide in M3.0.)

**Never persist customer scans in the repo** (existing File & Data rule). `tmp/` and
`output/` handling unchanged; test fixtures are synthetic renders (§10).

## 4. Segmentation — full newspaper pages (M3.3, probe designed now)

A clipping is one obituary; a scanned *page* from a 1940s weekly may hold six, plus ads.
Stage 2 is a single cheap vision call: *"Identify each distinct obituary on this image;
return count + approximate bounding regions + the deceased's name for each."*

- 1 result → job continues as-is (the common clipping case; probe cost is one small call).
- N results → N child jobs, each transcribing a cropped region; the parent page image is the
  shared provenance root. Review UI lists children; each is reviewed/approved independently.
- Regions are advisory: crops include generous margins, and the transcription prompt says
  "transcribe only the obituary of {name}, ignore adjacent text."

M3.1 ships with the probe present but the N>1 branch returning "multi-obituary pages arrive
in M3.3" — the data model (parent/child jobs) exists from day one so nothing needs migrating.

## 5. Transcription design (M3.1)

One vision call per obituary region. Output is JSON (same fenceless discipline as the
extraction prompt), stored as `transcript.json`:

```json
{
  "text": "NEESE — Donna Sue Neese, age 86, of Pleasant Valley...",
  "illegible_spans": [
    {"marker": "[illegible:1]", "guess": "Trenton", "reason": "ink blot over word"}
  ],
  "layout_notes": "two-column clipping, headline set in caps, portrait photo top-right",
  "portrait": {"present": true, "region": "top-right", "caption": null},
  "header_context": {"newspaper": "Gallatin North Missourian", "page_date": "visible: Dec 18, 2025"}
}
```

Prompt rules (new file `prompts/obituary_transcribe.md`):

1. **Verbatim, not cleaned.** Preserve original spelling, including errors — the transcript
   is evidence. (The extraction stage is where interpretation happens.)
2. **Never silently guess.** Unreadable text becomes `[illegible:n]` inline, with the
   best guess and reason in `illegible_spans` — the reviewer sees exactly where the model
   was unsure of the *pixels*, distinct from extraction uncertainty.
3. **Capture free context.** Masthead/page-date/section headers visible in the scan go in
   `header_context` — this often supplies the citation (newspaper name + date) that pasted
   text never has.
4. **Portrait detection only** — presence + region. Cropping/uploading the portrait as a
   FamilySearch Photo Memory is M3.5 with the rest of the Memories work.

**Model choice:** default Sonnet (repo's standing plan; degraded newsprint is genuinely hard).
M3.0 measures Haiku 4.5 vision on the same fixtures — if Haiku transcribes clean modern
clippings adequately, a quality/cost tier (Haiku first, Sonnet on low-confidence retry)
becomes a M3.4 batch-cost lever. Measure first, don't assume.

**BYOK note:** transcription roughly doubles per-obit Anthropic spend vs. text paste (§9.2).
Fine under BYOK; matters for pricing the Matthew Johnson engagement.

## 6. Extraction confidence & evidence (M3.2)

The extraction prompt gains a second output block — per-field meta, keyed by JSON path:

```json
"_meta": {
  "deceased.birth_date": {"confidence": "high",   "evidence": "born July 25, 1939, in Jamesport"},
  "deceased.maiden_name": {"confidence": "low",    "evidence": null,
                           "note": "no parenthetical; surname may be married name"},
  "relationships.children[1].deceased": {"confidence": "medium",
                           "evidence": "preceded in death by ... a son, James"}
}
```

- **Confidence is categorical** (`high` / `medium` / `low`), not numeric. LLM numeric
  self-scores are noise with decimals; three buckets map directly to UI treatment
  (plain / amber / red-outline) and are honest about the resolution we actually have.
- **Evidence is a verbatim quote** from the input text supporting the value. This is the
  anti-hallucination check: post-extraction, code verifies each evidence string actually
  appears in the transcript (fuzzy match to tolerate OCR hyphenation); a value whose
  evidence doesn't ground gets demoted to `low` automatically.
- Deterministic lints add demotions the model can't be trusted to self-report:
  birth ≥ death date, death date in the future, child surname mismatch with no explanation,
  date that doesn't parse. Pure functions, unit-tested.
- Fields whose evidence lies inside an `[illegible:n]` guess inherit `low` from transcription
  — pixel uncertainty propagates forward.
- `_meta` is advisory throughout: it orders the reviewer's attention and is stored in
  provenance, but no value is auto-dropped and no write is auto-approved because of it.

Applies to *all* input channels (paste/URL too), so the schema change happens once.

## 7. Output schema v2

### 7.1 Shape

Schema v2 = v1 + additive keys, no breaking changes:

```json
{
  "schema_version": 2,
  "deceased": { ...unchanged... },
  "relationships": { ...unchanged... },
  "eulogy_text": "...", "service_details": "...",
  "source_url": "...",  "raw_text": "...",
  "_meta": { ...§6, absent in v1 files... },
  "provenance_ref": "Neese_Donna_Sue.provenance.json"
}
```

Readers (review UI, FWL-006 mapping layer) treat missing `schema_version` as v1. `raw_text`
for a scanned job holds the *transcript* text — so the FWL-006 Source Description note
(upload-plan §2.3) works unchanged.

### 7.2 What `source_url` means for a scan

Empty — there is no URL. The FWL-006 "pasted-text case" (one-line provenance string asked of
the user) is *replaced* for scans by the capture-metadata form (§8.1), which is strictly
richer. The citation string is assembled from it.

### 7.3 GEDCOM 7 file export — designed, deferred

A `gedcom7.py` pure exporter (approved JSON → single-family `.ged`) would serve users who
want RootsMagic/Ancestry instead of FamilySearch, and is trivially testable. But it is a new
product surface with zero committed users, and the FWL-006 GEDCOM X mapping already covers
the one integration that's real. **Deferred until someone asks; noted as a ~2-day, isolated
module when they do.** (Open question 5.)

---

## 8. Provenance model

### 8.1 The manifest

One `provenance.json` per job, append-only stages, written by `src/provenance.py`:

```json
{
  "job_id": "…", "parent_job_id": null,
  "scan": {
    "sha256": "…", "original_filename": "neese_clipping_003.tif",
    "media": "image/tiff", "pages": 1, "region": null,
    "capture_meta": {
      "newspaper": "Gallatin North Missourian", "publication_date": "2025-12-18",
      "page": "6", "collection": "DCHS obituary file, drawer 14",
      "entered_by": "operator", "auto_detected": {"newspaper": "…from header_context…"}
    }
  },
  "transcription": {"model": "claude-sonnet-5", "prompt": "obituary_transcribe.md@<git-sha>",
                     "ts": "…", "illegible_count": 1},
  "extraction":    {"model": "claude-haiku-4-5-20251001", "prompt": "obituary_extract.md@<git-sha>",
                     "ts": "…"},
  "review": {"reviewer": "local-user", "approved_ts": "…",
             "edits": [{"path": "deceased.birth_place", "from": "Jamesport, MO",
                         "to": "Jamesport, Daviess County, Missouri"}]},
  "familysearch": {"upload_journal": "…"}
}
```

- **Capture metadata form** at upload: newspaper, date, page, collection — pre-filled from
  the transcription's `header_context` when detected, confirmed by the human. For archivist
  batches this comes per-batch with per-item overrides (M3.4).
- **Prompt versions are git SHAs** of the prompt files — extraction behavior is reproducible.
- **Review edits are the diff** between machine JSON and approved JSON, computed at approve
  time (no keystroke logging). The human is a pipeline stage; their changes are provenance.
- **`familysearch`** links to the FWL-006 upload journal, closing the chain: pixels →
  transcript → JSON → edits → PIDs.

### 8.2 Provenance into FamilySearch (M3.5, extends FWL-006 §2.3)

For scanned jobs, the Source Description upgrade path: upload the scan image as a
**Memories document** on the subject person, and point the Source Description's `about` at
that memory — the citation becomes *inspectable* (anyone on FamilySearch sees the actual
clipping). Citation string assembled from `capture_meta`
(`"Obituary of Donna Sue Neese, Gallatin North Missourian, 18 Dec 2025, p. 6; DCHS obituary
file, drawer 14."`). Sequencing lives with FWL-006 M2.4/M2.3; the integration draft carries
the API details.

---

## 9. The archivist batch workflow (M3.4) — designed loosely, on purpose

What Matthew Johnson's team plausibly needs (unvalidated — see open questions):

- **Batch intake:** a folder/zip of scans + one capture-metadata sheet → queue of jobs.
- **Work queue UI:** list with states (`queued → transcribed → extracted → in-review →
  approved / rejected / needs-rescan`), assignable, resumable. The job-dir model already
  supports this; what's new is a listing UI and background workers instead of
  request-scoped processing.
- **Throughput math:** reviewer time will dominate (est. 1–3 min/obit vs. ~30 s machine
  time), so the queue optimizes reviewer flow — keyboard-first review, next-item auto-load —
  not API parallelism.

**Hard prerequisite: durable storage.** Render's free-tier filesystem is ephemeral
(repo-memory Known Issues) — fine for demo, disqualifying for a customer batch. Options:
Render Disk (cheapest change), S3/R2 (right answer if multi-tenant), or *on-prem/local
deployment at the customer* (archivists may prefer scans never leave their network — and it
sidesteps multi-tenancy entirely). Decision needs Matthew Johnson requirements (open
question 1) — do not build M3.4 before it.

### 9.2 Cost order-of-magnitude (verify in M3.0)

Per obituary, rough: transcription (Sonnet, one ~1.5–2.5k-token image + ~600-token output)
plus extraction (Haiku, unchanged) ≈ **$0.02–0.04**, i.e. roughly 2× the text-only path;
a 500-obit batch ≈ $10–20 of API spend against hours of reviewer labor. Pricing for the
archivist engagement should charge for the review workflow, not the tokens. M3.0 replaces
these estimates with measurements.

---

## 10. Test strategy

- **Synthetic scan fixtures, generated not photographed:** render the existing text fixtures
  (Neese + demo samples) to images with Pillow — newsprint-ish column layout, then degraded
  variants (rotation, blur, noise, low contrast). Deterministic, no real personal data,
  regenerable. `tests/fixtures/scans/` + a `make fixtures` target.
- **Ground truth = the source text**, so transcription accuracy (CER/WER) is computable
  exactly. M3.0's model/resolution comparisons run on these.
- **Real-scan spot checks** (a DCHS clipping, a phone photo) stay manual browser smoke tests
  per house rules — never committed.
- `ingest.py`, `provenance.py`, evidence-grounding + lints (§6): pure, exhaustively
  unit-tested. Vision calls in tests: mocked transport, plus a gated
  `RUN_NETWORK_TESTS=1` integration case, matching the existing pattern.

---

## 11. Phased build plan

Linear chain; each phase independently demoable. Sizes are t-shirt, same convention as
FWL-006 §6. **Sequencing vs. Milestone 2:** M3.0–M3.2 have zero FamilySearch dependency and
can interleave with M2.x freely; only M3.5 waits on M2.3.

| # | Phase | Contents | Acceptance | Size |
| --- | --- | --- | --- | --- |
| **M3.0** | Fixtures + model eval | Synthetic scan fixture generator; transcription prompt v1; Sonnet-vs-Haiku and resolution-knee measurements; cost table; PDF-input decision (§3) | Written eval note in repo (accuracy + cost per model/resolution); chosen defaults recorded | S |
| **M3.1** | Single-scan happy path | `ingest.py`, `transcribe.py`, upload route, segmentation probe (single-obit branch), 3-pane review (image + transcript + fields) | A fixture scan uploads → correct fields in review UI → approved JSON in `output/`; tests green | M |
| **M3.2** | Confidence + provenance | `_meta` (all input channels), evidence-grounding check, lints, schema v2, `provenance.py` + capture-metadata form, low-confidence UI highlighting | Golden-file manifest for the Neese scan fixture; a planted date-contradiction fixture surfaces as `low` in UI | M |
| **M3.3** | Page segmentation | N>1 branch: child jobs, cropping, per-child review | Synthetic 3-obit page fixture → 3 reviewable child jobs, no text bleed between them | M |
| **M3.4** | Batch + queue | Batch intake, queue UI, background processing, durable storage (decision from open q.1), keyboard-first review | 20-fixture batch processed end-to-end; survives app restart mid-batch | L |
| **M3.5** | FS provenance closure | Scan → Memories document upload; Source `about` → memory; portrait crop → Photo Memory; citation from `capture_meta` | Beta person shows attached clipping image as the cited source | M *(needs M2.3)* |

---

## 12. Open questions (for Joel / stakeholders)

1. **Matthew Johnson requirements** *(blocks M3.4 shape)* — batch sizes, formats (TIFF?
   multi-page PDF?), does capture metadata exist in their scanning software already, can
   scans leave their premises (→ hosted vs. on-prem), who reviews?
2. **Model floor for transcription** *(M3.0 answers empirically)* — is Haiku 4.5 vision
   good enough for clean clippings?
3. **Handwritten material** — obituary *cards* and funeral-home ledgers are sometimes
   handwritten. In scope ever? (Vision models can, accuracy varies wildly.) Proposed: out of
   scope for M3, revisit with real samples.
4. **Capture-metadata burden** — is a required newspaper/date form acceptable friction for
   the library-patron case, or should it be optional with a weaker citation?
5. **GEDCOM 7 export** (§7.3) — anyone actually asking? Deferred until yes.
6. **Pre-1930 newsprint** — fixture degradation is a proxy; real century-old microfilm
   scans may need a preprocessing pass after all. Get real samples from DCHS early (M3.0
   stretch).

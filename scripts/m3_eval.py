"""M3.0 vision transcription eval — Sonnet 5 vs Haiku 4.5, resolution knee,
segmentation-probe accuracy, and PDF-vs-per-page comparison.

Network use: Anthropic API only (per OM2 §8.6 and the FWL-008 handoff).
Costs are computed from actual response.usage token counts.

Usage:
    python scripts/m3_eval.py matrix    # model x fixture x resolution grid
    python scripts/m3_eval.py segment   # multi-obit page: probe + crop vs name-targeted
    python scripts/m3_eval.py pdf       # 2-page PDF input vs per-page images
    python scripts/m3_eval.py all

Results append to tmp/m3_eval_results.jsonl; each command prints a summary table.
"""

import base64
import io
import json
import sys
import time
from pathlib import Path

import anthropic
from dotenv import load_dotenv
from PIL import Image

from eval_metrics import cer, iou, normalize, wer

REPO = Path(__file__).resolve().parent.parent
SCANS = REPO / "tests" / "fixtures" / "scans"
RESULTS = REPO / "tmp" / "m3_eval_results.jsonl"
PROMPT = (REPO / "prompts" / "obituary_transcribe.md").read_text(encoding="utf-8")

# Intro pricing (USD per MTok) in effect on eval date 2026-07-11.
# Sonnet 5 standard is $3/$15 after 2026-08-31 — the eval note scales this.
PRICING = {
    "claude-sonnet-5": (2.00, 10.00),
    "claude-haiku-4-5": (1.00, 5.00),
}
MODELS = list(PRICING)

load_dotenv(REPO / ".env")
client = anthropic.Anthropic()


def encode_image(path: Path, long_edge: int | None) -> tuple[str, str, tuple[int, int]]:
    """Load, optionally downscale (never upscale), return (b64, media_type, size)."""
    img = Image.open(path).convert("RGB")
    if long_edge and max(img.size) > long_edge:
        scale = long_edge / max(img.size)
        img = img.resize((round(img.size[0] * scale), round(img.size[1] * scale)),
                         Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return base64.standard_b64encode(buf.getvalue()).decode(), "image/png", img.size


def parse_json_response(raw: str) -> tuple[dict, bool]:
    """Parse the model's JSON; tolerate fences; fall back to raw-as-text."""
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text[text.find("{"):text.rfind("}") + 1]
    try:
        return json.loads(text), True
    except json.JSONDecodeError:
        return {"text": raw, "illegible_spans": [], "header_context": {}}, False


def call_model(model: str, content: list, max_tokens: int = 4096) -> tuple[dict, dict]:
    """One transcription call. Returns (parsed_payload, run_meta)."""
    kwargs = {}
    if model == "claude-sonnet-5":
        # Haiku 4.5 runs without thinking by default; disable on Sonnet 5 so the
        # model comparison is apples-to-apples on cost and latency.
        kwargs["thinking"] = {"type": "disabled"}
    t0 = time.monotonic()
    resp = client.messages.create(
        model=model, max_tokens=max_tokens, system=PROMPT,
        messages=[{"role": "user", "content": content}], **kwargs,
    )
    latency = time.monotonic() - t0
    raw = "".join(b.text for b in resp.content if b.type == "text")
    payload, parse_ok = parse_json_response(raw)
    in_tok, out_tok = resp.usage.input_tokens, resp.usage.output_tokens
    p_in, p_out = PRICING[model]
    meta = {
        "model": model, "input_tokens": in_tok, "output_tokens": out_tok,
        "cost_usd": round((in_tok * p_in + out_tok * p_out) / 1e6, 6),
        "latency_s": round(latency, 1), "parse_ok": parse_ok,
        "stop_reason": resp.stop_reason,
    }
    return payload, meta


def transcribe(model: str, image_path: Path, long_edge: int | None,
               target_name: str | None = None) -> tuple[dict, dict]:
    b64, media, size = encode_image(image_path, long_edge)
    ask = ("Transcribe this obituary." if not target_name else
           f"Transcribe only the obituary of {target_name}; ignore all other text.")
    payload, meta = call_model(model, [
        {"type": "image", "source": {"type": "base64", "media_type": media, "data": b64}},
        {"type": "text", "text": ask},
    ])
    meta["sent_px"] = list(size)
    return payload, meta


def record(row: dict) -> None:
    RESULTS.parent.mkdir(exist_ok=True)
    with RESULTS.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")


def score(payload: dict, gt_text: str) -> dict:
    hyp = payload.get("text") or ""
    return {"cer": round(cer(hyp, gt_text), 4), "wer": round(wer(hyp, gt_text), 4),
            "illegible": len(payload.get("illegible_spans") or [])}


# ---------------------------------------------------------------- matrix ----

MATRIX_FIXTURES = ["neese_clean", "neese_degraded", "neese_phone", "veteran_clean"]
MATRIX_EDGES = [512, 768, 1092, 1568]


def run_matrix() -> None:
    rows = []
    for fixture in MATRIX_FIXTURES:
        img = next(SCANS.glob(f"{fixture}.*g"))  # .png or .jpg
        gt = (SCANS / f"{fixture}.gt.txt").read_text(encoding="utf-8")
        edges = list(MATRIX_EDGES)
        if fixture in ("neese_degraded", "veteran_clean"):
            edges.append(None)  # native resolution (Sonnet high-res check)
        for edge in edges:
            for model in MODELS:
                if edge is None and model != "claude-sonnet-5":
                    continue
                try:
                    payload, meta = transcribe(model, img, edge)
                except anthropic.APIError as e:
                    record({"phase": "matrix", "fixture": fixture, "long_edge": edge,
                            "model": model, "error": str(e)})
                    print(f"ERROR {fixture} {edge} {model}: {e}")
                    continue
                row = {"phase": "matrix", "fixture": fixture,
                       "long_edge": edge or "native",
                       "header_context": payload.get("header_context"),
                       **meta, **score(payload, gt)}
                record(row)
                rows.append(row)
                print(f"{fixture:16} edge={str(edge or 'native'):6} {model:18} "
                      f"CER={row['cer']:.3f} WER={row['wer']:.3f} "
                      f"in={row['input_tokens']} out={row['output_tokens']} "
                      f"${row['cost_usd']:.4f} {row['latency_s']}s")
    _summarize(rows)


def _summarize(rows: list) -> None:
    total = sum(r.get("cost_usd", 0) for r in rows)
    print(f"\n{len(rows)} calls, total ${total:.3f}")


# --------------------------------------------------------------- segment ----

SEGMENT_PROMPT = """Identify each distinct obituary on this newspaper page image.
Return ONLY valid JSON (no fences): {"count": N, "obituaries": [{"name": "deceased's full name as printed", "bbox": {"left": 0.0, "top": 0.0, "right": 0.0, "bottom": 0.0}}]}
bbox values are fractions of image width/height. Include ONLY obituaries — ignore mastheads, page headers, and advertisements."""


def _match_gt(name: str, gt_obits: list) -> dict | None:
    n = normalize(name)
    for o in gt_obits:
        if normalize(o["name"]) in n or n in normalize(o["name"]):
            return o
    return None


def run_segment() -> None:
    page = SCANS / "page_3obits.png"
    gt = json.loads((SCANS / "page_3obits.json").read_text(encoding="utf-8"))
    page_img = Image.open(page)

    for model in MODELS:
        b64, media, size = encode_image(page, 1568)
        payload, meta = call_model(model, [
            {"type": "image", "source": {"type": "base64", "media_type": media, "data": b64}},
            {"type": "text", "text": SEGMENT_PROMPT},
        ])
        found = payload.get("obituaries") or []
        ious = {}
        for f in found:
            m = _match_gt(f.get("name", ""), gt["obituaries"])
            if m and isinstance(f.get("bbox"), dict):
                b = f["bbox"]
                ious[m["name"]] = round(iou(
                    [b.get("left", 0), b.get("top", 0), b.get("right", 0), b.get("bottom", 0)],
                    m["bbox"]), 3)
        row = {"phase": "segment-probe", "count_found": len(found),
               "count_true": len(gt["obituaries"]), "iou_by_name": ious,
               "raw_boxes": found, **meta}
        record(row)
        print(f"probe {model}: found {len(found)}/{len(gt['obituaries'])}, IoU {ious}, "
              f"${meta['cost_usd']:.4f}")

        # Downstream comparison: crop-from-predicted-bbox vs name-targeted full page.
        for gt_obit in gt["obituaries"]:
            pred = next((f for f in found
                         if _match_gt(f.get("name", ""), [gt_obit])), None)
            # (a) transcribe the predicted-region crop (3% margin), native res
            if pred and isinstance(pred.get("bbox"), dict):
                b = pred["bbox"]
                W, H = page_img.size
                m = 0.03
                crop = page_img.crop((max(0, (b["left"] - m)) * W, max(0, (b["top"] - m)) * H,
                                      min(1, (b["right"] + m)) * W, min(1, (b["bottom"] + m)) * H))
                tmp = REPO / "tmp" / "seg_crop.png"
                tmp.parent.mkdir(exist_ok=True)
                crop.save(tmp)
                payload_c, meta_c = transcribe(model, tmp, 1568)
                row = {"phase": "segment-crop", "target": gt_obit["name"],
                       **meta_c, **score(payload_c, gt_obit["text"])}
                record(row)
                print(f"  crop {model} {gt_obit['name']:24} CER={row['cer']:.3f} "
                      f"in={row['input_tokens']} ${row['cost_usd']:.4f}")
            # (b) full-page, name-targeted
            payload_n, meta_n = transcribe(model, page, 1568, target_name=gt_obit["name"])
            row = {"phase": "segment-name-targeted", "target": gt_obit["name"],
                   **meta_n, **score(payload_n, gt_obit["text"])}
            record(row)
            print(f"  name {model} {gt_obit['name']:24} CER={row['cer']:.3f} "
                  f"in={row['input_tokens']} ${row['cost_usd']:.4f}")


# ------------------------------------------------------------------- pdf ----

def run_pdf() -> None:
    """2-page PDF (neese_clean + veteran_clean at 1568 long edge) vs the same
    pages sent as individual images (matrix already measured those)."""
    pages = []
    for name in ("neese_clean", "veteran_clean"):
        img = Image.open(SCANS / f"{name}.png").convert("RGB")
        scale = 1568 / max(img.size)
        pages.append(img.resize((round(img.size[0] * scale), round(img.size[1] * scale)),
                                Image.LANCZOS))
    buf = io.BytesIO()
    pages[0].save(buf, "PDF", save_all=True, append_images=pages[1:], resolution=150)
    pdf_b64 = base64.standard_b64encode(buf.getvalue()).decode()

    ask = ('This PDF contains one obituary per page. For EACH page, apply the '
           'transcription rules and return ONLY valid JSON of the form '
           '{"pages": [{"page": 1, "text": "..."}, {"page": 2, "text": "..."}]} '
           'where each "text" is the verbatim transcript (headline first).')
    for model in MODELS:
        try:
            payload, meta = call_model(model, [
                {"type": "document",
                 "source": {"type": "base64", "media_type": "application/pdf", "data": pdf_b64}},
                {"type": "text", "text": ask},
            ], max_tokens=8192)
        except anthropic.APIError as e:
            record({"phase": "pdf", "model": model, "error": str(e)})
            print(f"ERROR pdf {model}: {e}")
            continue
        page_texts = {p.get("page"): p.get("text", "") for p in payload.get("pages", [])}
        scores = {}
        for i, name in enumerate(("neese_clean", "veteran_clean"), 1):
            gt = (SCANS / f"{name}.gt.txt").read_text(encoding="utf-8")
            scores[name] = {"cer": round(cer(page_texts.get(i, ""), gt), 4),
                            "wer": round(wer(page_texts.get(i, ""), gt), 4)}
        row = {"phase": "pdf", "scores": scores, **meta}
        record(row)
        print(f"pdf {model}: {scores} in={meta['input_tokens']} out={meta['output_tokens']} "
              f"${meta['cost_usd']:.4f} {meta['latency_s']}s")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "all"
    if cmd in ("matrix", "all"):
        run_matrix()
    if cmd in ("segment", "all"):
        run_segment()
    if cmd in ("pdf", "all"):
        run_pdf()

"""Generate synthetic scanned-obituary fixtures for the M3.0 vision eval.

Renders the demo obituary texts (synthetic, anonymized) as newsprint-style
clipping images, plus degraded variants and one multi-obituary page with
ground-truth bounding boxes. Deterministic for a given seed on a given
machine (font rendering varies across OSes, so byte-identity is guaranteed
per-machine, not cross-platform).

Usage:  python scripts/gen_fixtures.py  (or: make fixtures)
Output: tests/fixtures/scans/  (gitignored — regenerate, never commit)
"""

import hashlib
import io
import json
import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "tests" / "fixtures" / "scans"
SEED = 42

# Newsprint-ish rendering constants (native render is deliberately larger
# than any eval resolution so downscaling is real).
BG = (243, 240, 233)
INK = (28, 26, 24)
BODY_PX = 34
HEAD_PX = 54
MAST_PX = 30
LINE_GAP = 12
COL_TEXT_W = 860
MARGIN = 60

FONT_CANDIDATES = {
    "body": ["C:/Windows/Fonts/times.ttf", "C:/Windows/Fonts/georgia.ttf",
             "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf"],
    "bold": ["C:/Windows/Fonts/georgiab.ttf", "C:/Windows/Fonts/timesbd.ttf",
             "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf"],
}


def _font(kind: str, px: int) -> ImageFont.FreeTypeFont:
    for path in FONT_CANDIDATES[kind]:
        if Path(path).exists():
            return ImageFont.truetype(path, px)
    raise RuntimeError(f"No usable {kind} font found; install a serif TTF")


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_w: int) -> list[str]:
    lines, line = [], ""
    for word in text.split():
        trial = f"{line} {word}".strip()
        if draw.textlength(trial, font=font) <= max_w:
            line = trial
        else:
            if line:
                lines.append(line)
            line = word
    if line:
        lines.append(line)
    return lines


def headline_for(text: str) -> str:
    """Deceased's name (text up to ', age'), uppercased — the clipping headline."""
    name = text.split(", age")[0].strip()
    return name.upper()


def render_clipping(text: str, masthead: str | None = None) -> tuple[Image.Image, str]:
    """Render one obituary as a single-column clipping.

    Returns (image, ground_truth_text). Ground truth = headline + body,
    excluding any masthead (the transcription prompt routes mastheads to
    header_context, not text).
    """
    body_f, head_f, mast_f = _font("body", BODY_PX), _font("bold", HEAD_PX), _font("body", MAST_PX)
    probe = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    headline = headline_for(text)
    head_lines = _wrap(probe, headline, head_f, COL_TEXT_W)
    body_lines = _wrap(probe, text, body_f, COL_TEXT_W)

    h = MARGIN
    if masthead:
        h += MAST_PX + 34
    h += len(head_lines) * (HEAD_PX + LINE_GAP) + 20
    h += len(body_lines) * (BODY_PX + LINE_GAP) + MARGIN
    w = COL_TEXT_W + 2 * MARGIN

    img = Image.new("RGB", (w, h), BG)
    d = ImageDraw.Draw(img)
    y = MARGIN
    if masthead:
        d.text((MARGIN, y), masthead, font=mast_f, fill=INK)
        y += MAST_PX + 10
        d.line([(MARGIN, y), (w - MARGIN, y)], fill=INK, width=3)
        y += 24
    for ln in head_lines:
        d.text((MARGIN, y), ln, font=head_f, fill=INK)
        y += HEAD_PX + LINE_GAP
    y += 20
    for ln in body_lines:
        d.text((MARGIN, y), ln, font=body_f, fill=INK)
        y += BODY_PX + LINE_GAP
    return img, f"{headline}\n{text}"


def degrade(img: Image.Image, rng: random.Random) -> Image.Image:
    """Aged-newsprint variant: rotation, blur, noise, low contrast, JPEG artifacts."""
    out = img.rotate(1.8, expand=True, fillcolor=BG, resample=Image.BICUBIC)
    out = out.filter(ImageFilter.GaussianBlur(1.1))
    noise = Image.frombytes("L", out.size, rng.randbytes(out.size[0] * out.size[1]))
    out = Image.composite(out, Image.new("RGB", out.size, (90, 85, 78)),
                          noise.point(lambda p: 255 - min(p, 40)))
    out = ImageEnhance.Contrast(out).enhance(0.62)
    out = ImageEnhance.Brightness(out).enhance(1.08)
    buf = io.BytesIO()
    out.save(buf, "JPEG", quality=32)
    return Image.open(buf).convert("RGB")


def phone_photo(img: Image.Image) -> Image.Image:
    """Library-patron variant: skewed phone photo with uneven lighting."""
    out = img.rotate(-3.2, expand=True, fillcolor=(120, 115, 108), resample=Image.BICUBIC)
    shade = Image.new("L", out.size)
    for x in range(out.size[0]):  # left-to-right brightness falloff
        shade.paste(int(60 * x / out.size[0]), (x, 0, x + 1, out.size[1]))
    out = Image.composite(Image.new("RGB", out.size, (35, 32, 30)), out, shade)
    return out.filter(ImageFilter.GaussianBlur(0.8))


def render_page(obits: list[tuple[str, str]]) -> tuple[Image.Image, dict]:
    """Full newspaper page: 2 columns, 3 obituaries + a filler ad box.

    Returns (image, ground_truth) where ground_truth has per-obit fractional
    bboxes [left, top, right, bottom] and text — segmentation-probe truth.
    """
    W, H = 2100, 2900
    col_w = (W - 3 * MARGIN) // 2
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    mast_f = _font("bold", 44)
    d.text((MARGIN, 40), "THE GALLATIN NORTH MISSOURIAN", font=mast_f, fill=INK)
    d.text((MARGIN, 96), "Obituaries — Thursday, March 12, 2026 — Page 6",
           font=_font("body", MAST_PX), fill=INK)
    d.line([(MARGIN, 150), (W - MARGIN, 150)], fill=INK, width=4)
    d.line([(W // 2, 170), (W // 2, H - MARGIN)], fill=INK, width=2)

    gt = {"page": "page_3obits.png", "obituaries": []}
    slots = [(MARGIN, 190), (MARGIN, None), (W // 2 + MARGIN // 2, 190)]  # col1 top, col1 below, col2 top
    y_after_first = None
    for i, (name, text) in enumerate(obits):
        x, y = slots[i]
        if y is None:
            y = y_after_first + 40
        clip, _ = render_clipping(text)
        scale = col_w / clip.size[0]
        clip = clip.resize((col_w, int(clip.size[1] * scale)), Image.LANCZOS)
        img.paste(clip, (x, y))
        if i == 0:
            y_after_first = y + clip.size[1]
            d.line([(x, y_after_first + 20), (x + col_w, y_after_first + 20)], fill=INK, width=2)
        gt["obituaries"].append({
            "name": name,
            "bbox": [x / W, y / H, (x + col_w) / W, (y + clip.size[1]) / H],
            "text": f"{headline_for(text)}\n{text}",
        })

    # Filler ad in column 2 below the third obit — segmentation must skip it.
    last = gt["obituaries"][2]["bbox"]
    ay = int(last[3] * H) + 60
    ad_f = _font("bold", 40)
    d.rectangle([W // 2 + MARGIN // 2, ay, W - MARGIN, min(ay + 420, H - MARGIN)], outline=INK, width=4)
    for j, ln in enumerate(["ANNUAL SPRING CRAFT FAIR", "Daviess County Fairgrounds",
                            "Saturday, March 21 — 9am to 4pm", "Booth rentals: 660-555-0142"]):
        d.text((W // 2 + MARGIN // 2 + 40, ay + 50 + j * 90), ln, font=ad_f, fill=INK)
    return img, gt


def main() -> None:
    rng = random.Random(SEED)
    OUT.mkdir(parents=True, exist_ok=True)
    sources = {
        "neese": (REPO / "demo" / "sample_neese.txt").read_text(encoding="utf-8").strip(),
        "veteran": (REPO / "demo" / "sample_veteran.txt").read_text(encoding="utf-8").strip(),
        "amish": (REPO / "demo" / "sample_amish.txt").read_text(encoding="utf-8").strip(),
    }
    manifest = {"seed": SEED, "files": {}}

    def save(name: str, img: Image.Image, gt_text: str | None = None) -> None:
        path = OUT / name
        img.save(path)
        manifest["files"][name] = hashlib.sha256(path.read_bytes()).hexdigest()
        if gt_text is not None:
            (OUT / f"{Path(name).stem}.gt.txt").write_text(gt_text, encoding="utf-8")

    masthead = "Gallatin North Missourian — Thursday, December 18, 2025 — Page 6"
    for key, text in sources.items():
        clean, gt_text = render_clipping(text, masthead=masthead if key == "neese" else None)
        save(f"{key}_clean.png", clean, gt_text)
        if key == "neese":
            save("neese_degraded.jpg", degrade(clean, rng), gt_text)
            save("neese_phone.png", phone_photo(clean), gt_text)

    page, gt = render_page([("Donna Sue Neese", sources["neese"]),
                            ("Harold Dean Whitaker", sources["veteran"]),
                            ("Ada Mae (Yoder) Miller", sources["amish"])])
    save("page_3obits.png", page)
    (OUT / "page_3obits.json").write_text(json.dumps(gt, indent=2), encoding="utf-8")

    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"Wrote {len(manifest['files'])} images + ground truth to {OUT}")


if __name__ == "__main__":
    main()

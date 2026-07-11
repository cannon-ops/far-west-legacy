"""Pure text/geometry metrics for the M3.0 vision eval. No dependencies."""


def normalize(text: str) -> str:
    """Case-fold and collapse whitespace so CER/WER measure content, not layout."""
    return " ".join(text.lower().split())


def levenshtein(a, b) -> int:
    """Edit distance between two sequences (strings or token lists)."""
    if len(a) < len(b):
        a, b = b, a
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        curr = [i]
        for j, cb in enumerate(b, 1):
            curr.append(min(prev[j] + 1, curr[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = curr
    return prev[-1]


def cer(hypothesis: str, reference: str) -> float:
    """Character error rate on normalized text (0.0 = perfect)."""
    ref = normalize(reference)
    return levenshtein(normalize(hypothesis), ref) / max(len(ref), 1)


def wer(hypothesis: str, reference: str) -> float:
    """Word error rate on normalized text (0.0 = perfect)."""
    ref = normalize(reference).split()
    return levenshtein(normalize(hypothesis).split(), ref) / max(len(ref), 1)


def iou(box_a, box_b) -> float:
    """Intersection-over-union of two [left, top, right, bottom] boxes
    (fractional or pixel coordinates, as long as both use the same units)."""
    il = max(box_a[0], box_b[0])
    it = max(box_a[1], box_b[1])
    ir = min(box_a[2], box_b[2])
    ib = min(box_a[3], box_b[3])
    if ir <= il or ib <= it:
        return 0.0
    inter = (ir - il) * (ib - it)
    area_a = (box_a[2] - box_a[0]) * (box_a[3] - box_a[1])
    area_b = (box_b[2] - box_b[0]) * (box_b[3] - box_b[1])
    return inter / (area_a + area_b - inter)

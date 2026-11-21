"""Validate Surya 2.5 OCR block coordinates; never perform redaction.

For anonymization, block rectangles are not the geometry of words. This
experiment provides a safe preview only and exits nonzero on invalid or
missing coordinates. No PDF files, patient data, or network requests.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any


class CoordinateError(ValueError):
    """OCR returned invalid or inadequate coordinates."""


def _box(value: Any, *, bounds: tuple[float, float] | None = None) -> tuple[float, float, float, float]:
    if (
        not isinstance(value, (list, tuple))
        or len(value) != 4
        or any(type(v) not in (float, int) or not math.isfinite(v) for v in value)
    ):
        raise CoordinateError("bbox is missing, non-numeric or non-finite")
    x0, y0, x1, y1 = map(float, value)
    if x0 < 0 or y0 < 0 or x1 <= x0 or y1 <= y0:
        raise CoordinateError("bbox has invalid dimensions")
    if bounds and (x1 > bounds[0] + 1 or y1 > bounds[1] + 1):
        raise CoordinateError("bbox exceeds the page dimensions")
    return x0, y0, x1, y1


def validate_surya_blocks(payload: Any) -> tuple[tuple[float, float], list[tuple[float, float, float, float]]]:
    """Require Surya **2.5** blocks + image_bbox on a single synthetic page.

    The public Surya 2.5 JSON schema is a dict keyed by source stem whose
    values are lists of pages. Blocks carry HTML and bboxes. Unlike older
    Surya OCR schemas, v2.5 block boxes do NOT establish word coordinates.
    """
    if not isinstance(payload, dict) or len(payload) != 1:
        raise CoordinateError("unexpected Surya 2.5 result structure")
    pages = next(iter(payload.values()))
    if not isinstance(pages, list) or len(pages) != 1 or not isinstance(pages[0], dict):
        raise CoordinateError("expected exactly one synthetic test page")
    page = pages[0]
    image_box = _box(page.get("image_bbox"))
    if image_box[0] != 0 or image_box[1] != 0:
        raise CoordinateError("unexpected image coordinate origin")
    width, height = image_box[2], image_box[3]
    blocks = page.get("blocks")
    if not isinstance(blocks, list) or not blocks:
        raise CoordinateError("no OCR blocks with coordinates; OCR text alone is insufficient")
    found: list[tuple[float, float, float, float]] = []
    for block in blocks:
        if not isinstance(block, dict):
            raise CoordinateError("invalid OCR block")
        if block.get("error"):
            raise CoordinateError("Surya reported OCR failure in a block")
        if block.get("skipped"):
            continue
        html = block.get("html")
        if not isinstance(html, str):
            raise CoordinateError("OCR block has no text/HTML field")
        if not html.strip():
            continue
        found.append(_box(block.get("bbox"), bounds=(width, height)))
    if not found:
        raise CoordinateError("no text blocks have valid coordinates")
    return (width, height), found


def create_bbox_preview(
    image_path: Path,
    output_path: Path,
    source_dimensions: tuple[float, float],
    boxes: list[tuple[float, float, float, float]],
) -> None:
    """Draw OUTLINES for visual verification only, not black/white masking."""
    from PIL import Image, ImageDraw

    with Image.open(image_path) as original:
        page = original.convert("RGB")
    scale_x = page.width / source_dimensions[0]
    scale_y = page.height / source_dimensions[1]
    draw = ImageDraw.Draw(page)
    for x0, y0, x1, y1 in boxes:
        draw.rectangle(
            (int(x0 * scale_x), int(y0 * scale_y),
             int(x1 * scale_x), int(y1 * scale_y)),
            outline=(210, 30, 30), width=3,
        )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    page.save(output_path, format="PNG")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate OCR block bboxes and optionally draw a synthetic preview; NEVER redact data."
    )
    parser.add_argument("report", type=Path)
    parser.add_argument("--image", type=Path)
    parser.add_argument("--preview", type=Path)
    args = parser.parse_args(argv)
    if bool(args.image) != bool(args.preview):
        parser.error("specify --image and --preview together")
    try:
        payload = json.loads(args.report.read_text(encoding="utf-8"))
        dimensions, boxes = validate_surya_blocks(payload)
        if args.preview:
            create_bbox_preview(args.image, args.preview, dimensions, boxes)
        print(f"Surya 2.5 BBOX OK: {len(boxes)} text blocks; {dimensions[0]:g}x{dimensions[1]:g} px.")
        print("WORD BBOX: NOT VERIFIED. These are block boxes, NOT word locations.")
        print("ANONYMIZATION: NOT PERFORMED; no patient PDFs processed.")
        return 0
    except (OSError, ValueError, ImportError) as exc:
        print(f"Surya 2.5 BBOX ERROR: {type(exc).__name__}; anonymization unavailable.")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

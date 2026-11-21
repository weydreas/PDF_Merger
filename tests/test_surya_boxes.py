"""Surya 2.5 coordinate contract: synthetics only, no PDF or AI access."""
from contextlib import redirect_stdout
from io import StringIO
import json
import math
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from inspect_surya_boxes import CoordinateError, main, validate_surya_blocks


def example(box=None, *, text="ФИО: Иванов Иван (ВЫМЫШЛЕННЫЙ)"):
    if box is None:
        box = [12, 18, 160, 48]
    return {
        "synthetic-medical-page": [{
            "image_bbox": [0, 0, 640, 200],
            "blocks": [{
                "label": "Text",
                "html": "<p>" + text + "</p>",
                "bbox": box,
                "polygon": [[12, 18], [160, 18], [160, 48], [12, 48]],
                "confidence": 0.88,
                "skipped": False,
                "error": False,
            }],
        }],
    }


class SuryaCoordinatesTests(unittest.TestCase):
    def test_accepts_actual_v25_blocks_and_rejects_word_assumption(self):
        dimensions, boxes = validate_surya_blocks(example())
        self.assertEqual(dimensions, (640, 200))
        self.assertEqual(boxes, [(12, 18, 160, 48)])
        self.assertNotIn("words", example()["synthetic-medical-page"][0]["blocks"][0])

    def test_missing_coordinates_or_legacy_text_lines_fail_closed(self):
        for sample in (
            {"sample": [{"image_bbox": [0, 0, 640, 200], "text_lines": [{"text": "ФИО"}]}]},
            {"sample": [{"image_bbox": [0, 0, 640, 200], "blocks": []}]},
            {"sample": [{"blocks": [{"html": "ФИО", "bbox": [1, 2, 3, 4]}]}]},
            {"sample": [{"image_bbox": [0, 0, 640, 200], "blocks": [{"html": "ФИО"}]}]},
            {"sample": [{"image_bbox": [0, 0, 640, 200], "blocks": [{"html": "ФИО", "bbox": [1, 2, 3, 4], "error": True}]}]},
        ):
            with self.subTest(sample=sample):
                with self.assertRaises(CoordinateError):
                    validate_surya_blocks(sample)

    def test_bad_boxes_are_never_accepted(self):
        for invalid in (
            [10, 20, 9, 40], [1, 3, 20000, 100], [-1, 3, 50, 30],
            [0, 0, float("nan"), 100], [0, 0, float("inf"), 20],
            ["10", 12, 30, 40], [True, 10, 30, 40],
        ):
            with self.subTest(bbox=invalid):
                with self.assertRaises(CoordinateError):
                    validate_surya_blocks(example(invalid))

    def test_validator_never_leaks_ocr_text_in_console(self):
        with tempfile.TemporaryDirectory() as temp:
            data = Path(temp) / "result.json"
            data.write_text(json.dumps(example()), encoding="utf-8")
            stream = StringIO()
            with redirect_stdout(stream):
                rc = main([str(data)])
            self.assertEqual(rc, 0)
            output = stream.getvalue()
            self.assertIn("WORD BBOX: NOT VERIFIED", output)
            self.assertIn("ANONYMIZATION: NOT PERFORMED", output)
            self.assertNotIn("Иванов", output)

    def test_invalid_report_returns_nonzero_no_sensitive_text(self):
        with tempfile.TemporaryDirectory() as temp:
            data = Path(temp) / "result.json"
            data.write_text('{"sample":[{"blocks":[{"html":"Иванов Иван"}]}]}', encoding="utf-8")
            stream = StringIO()
            with redirect_stdout(stream):
                rc = main([str(data)])
            self.assertEqual(rc, 2)
            self.assertNotIn("Иванов", stream.getvalue())

    def test_cpu_pilot_does_not_accept_any_json_as_success(self):
        source = (ROOT / "scripts" / "test-surya-cpu.ps1").read_text(encoding="utf-8-sig")
        self.assertIn("inspect_surya_boxes.py", source)
        self.assertIn("results.json", source)
        self.assertIn("bbox-preview.png", source)
        self.assertIn("if ($LASTEXITCODE -ne 0)", source)
        self.assertIn("Текст получен, но координаты блоков", source)
        self.assertIn("results-' + [guid]::NewGuid()", source)
        self.assertNotIn("if ($jsonReports.Count -eq 0)", source)


if __name__ == "__main__":
    unittest.main()

"""Tests with synthetic blank PDFs only; no medical information."""

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
import tempfile
import unittest

from pypdf import PdfReader, PdfWriter

from pdf_merger import discover_groups, main, process


def make_pdf(path: Path, widths: list[float]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    writer = PdfWriter()
    for width in widths:
        writer.add_blank_page(width=width, height=100)
    with path.open("wb") as f:
        writer.write(f)
    writer.close()


class MergerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.input = self.root / "input"
        self.output = self.root / "output"
        self.input.mkdir()

    def run_process(self, **kwargs):
        with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            return process(self.input, self.output, **kwargs)

    def test_groups_pages_sorted_and_preserved(self):
        make_pdf(self.input / "2024-05-29-Бета.pdf", [202, 203])
        make_pdf(self.input / "2024-05-29-Альфа.PDF", [101])
        make_pdf(self.input / "2024-05-30-Иное.pdf", [301])
        report = self.run_process()
        self.assertEqual(report.groups_written, 2)
        self.assertEqual(sorted(x.name for x in self.output.iterdir()), ["2024-05-29.pdf", "2024-05-30.pdf"])
        reader = PdfReader(self.output / "2024-05-29.pdf")
        self.assertEqual([float(p.mediabox.width) for p in reader.pages], [101, 202, 203])

    def test_single_file_keeps_identical_bytes(self):
        path = self.input / "2026-01-01-only.pdf"
        make_pdf(path, [103])
        self.run_process()
        self.assertEqual((self.output / "2026-01-01.pdf").read_bytes(), path.read_bytes())

    def test_skips_existing_then_explicit_overwrite(self):
        file1 = self.input / "2025-02-13-first.pdf"
        make_pdf(file1, [111])
        self.run_process()
        file2 = self.input / "2025-02-13-second.pdf"
        make_pdf(file2, [222])
        r = self.run_process()
        self.assertEqual((r.groups_written, r.groups_skipped), (0, 1))
        self.assertEqual(len(PdfReader(self.output / "2025-02-13.pdf").pages), 1)
        r = self.run_process(overwrite=True)
        self.assertEqual(r.groups_written, 1)
        self.assertEqual(len(PdfReader(self.output / "2025-02-13.pdf").pages), 2)

    def test_dry_run_changes_nothing(self):
        make_pdf(self.input / "2024-02-29-leap.pdf", [120])
        self.run_process(dry_run=True)
        self.assertFalse(self.output.exists())

    def test_invalid_dates_and_unnamed_are_ignored(self):
        make_pdf(self.input / "2025-02-29-invalid.pdf", [130])
        make_pdf(self.input / "no-prefix.pdf", [140])
        make_pdf(self.input / "2024-02-29-valid.pdf", [150])
        r = self.run_process()
        self.assertEqual(r.files_ignored, 2)
        self.assertEqual(r.groups_written, 1)

    def test_corrupt_group_cannot_publish_partial_output(self):
        make_pdf(self.input / "2024-01-01-first.pdf", [101])
        (self.input / "2024-01-01-broken.pdf").write_bytes(b"not a pdf")
        make_pdf(self.input / "2024-01-02-ok.pdf", [102])
        r = self.run_process()
        self.assertEqual((r.groups_failed, r.groups_written), (1, 1))
        self.assertFalse((self.output / "2024-01-01.pdf").exists())
        self.assertTrue((self.output / "2024-01-02.pdf").exists())
        self.assertEqual(list(self.output.glob("*.tmp")), [])

    def test_recursive_excludes_output_subfolder(self):
        out = self.input / "output"
        make_pdf(self.input / "sub" / "2024-08-01-a.pdf", [111])
        make_pdf(out / "2024-08-01-old.pdf", [222])
        with redirect_stdout(StringIO()):
            r = process(self.input, out, recursive=True)
        self.assertEqual(r.groups_written, 1)
        self.assertEqual(len(PdfReader(out / "2024-08-01.pdf").pages), 1)

    def test_input_output_same_rejected(self):
        with self.assertRaisesRegex(ValueError, "must differ"):
            process(self.input, self.input)

    def test_cli_returns_nonzero_for_bad_input(self):
        with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            self.assertEqual(main(["--input-dir", str(self.root / "absent")]), 2)


if __name__ == "__main__":
    unittest.main()

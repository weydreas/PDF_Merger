"""Offline PDF merger: group PDF documents by YYYY-MM-DD filename prefix."""

from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile

from pypdf import PdfReader, PdfWriter


ROOT = Path(__file__).resolve().parent
DATE_PREFIX = re.compile(r"^(\d{4}-\d{2}-\d{2})-(.+)$")


@dataclass
class Result:
    groups_written: int = 0
    groups_skipped: int = 0
    groups_failed: int = 0
    files_ignored: int = 0
    groups_planned: int = 0


def discover_groups(input_dir: Path, output_dir: Path, recursive: bool = False) -> tuple[dict[str, list[Path]], list[Path]]:
    """Return date-grouped PDFs and ignored PDFs (no valid date prefix)."""
    grouped: dict[str, list[Path]] = defaultdict(list)
    ignored: list[Path] = []
    candidates = input_dir.rglob("*") if recursive else input_dir.iterdir()
    for path in candidates:
        # Do not follow shortcuts or symlinks to unexpected private locations.
        if path.is_symlink() or not path.is_file() or path.suffix.lower() != ".pdf":
            continue
        # --recursive: output may be located under input; never reprocess it.
        if path.is_relative_to(output_dir):
            continue
        match = DATE_PREFIX.fullmatch(path.stem)
        if not match:
            ignored.append(path)
            continue
        candidate = match.group(1)
        try:
            date.fromisoformat(candidate)
        except ValueError:
            ignored.append(path)
            continue
        grouped[candidate].append(path)

    def key(path: Path) -> tuple[str, str]:
        relative = str(path.relative_to(input_dir))
        return relative.casefold(), relative

    return {key_date: sorted(paths, key=key) for key_date, paths in sorted(grouped.items())}, sorted(ignored, key=key)


def _write_atomic(paths: list[Path], target: Path, overwrite: bool) -> None:
    """Prepare output locally, validate it, then publish without partial files."""
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(mode="wb", prefix=f".{target.stem}-", suffix=".tmp", dir=target.parent, delete=False) as handle:
            temp_path = Path(handle.name)

        if len(paths) == 1:
            # Keep lone PDFs byte-identical, including original metadata/signatures.
            expected_pages = len(PdfReader(str(paths[0]), strict=False).pages)
            shutil.copyfile(paths[0], temp_path)
        else:
            writer = PdfWriter()
            try:
                expected_pages = 0
                for source in paths:
                    reader = PdfReader(str(source), strict=False)
                    if reader.is_encrypted:
                        raise ValueError(f"Password-protected PDF: {source.name}")
                    expected_pages += len(reader.pages)
                    writer.append(reader, import_outline=False)
                with temp_path.open("wb") as handle:
                    writer.write(handle)
            finally:
                writer.close()

        actual_pages = len(PdfReader(str(temp_path), strict=False).pages)
        if actual_pages != expected_pages:
            raise ValueError(f"Page count mismatch: expected {expected_pages}, got {actual_pages}")

        if overwrite:
            os.replace(temp_path, target)
        else:
            # Same-directory hard link publishes a completed file atomically,
            # and raises FileExistsError instead of overwriting concurrently.
            os.link(temp_path, target)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def process(input_dir: Path, output_dir: Path, *, overwrite: bool = False, dry_run: bool = False, recursive: bool = False) -> Result:
    input_dir = input_dir.resolve()
    output_dir = output_dir.resolve()
    if not input_dir.is_dir():
        raise ValueError(f"Input directory does not exist: {input_dir}")
    if input_dir == output_dir:
        raise ValueError("Input and output directories must differ")

    groups, ignored = discover_groups(input_dir, output_dir, recursive=recursive)
    result = Result(files_ignored=len(ignored), groups_planned=len(groups))
    for path in ignored:
        print(f"IGNORED (invalid/missing YYYY-MM-DD-): {path.relative_to(input_dir)}")
    if not groups:
        print("No PDFs with a valid YYYY-MM-DD- prefix found.")
        return result

    if not dry_run:
        output_dir.mkdir(parents=True, exist_ok=True)

    for day, paths in groups.items():
        target = output_dir / f"{day}.pdf"
        print(f"\n{day}: {len(paths)} source PDF(s) -> {target.name}")
        for index, source in enumerate(paths, start=1):
            print(f"  {index}. {source.relative_to(input_dir)}")
        if target.exists() and not overwrite:
            print("  SKIPPED: destination already exists (use --overwrite to replace)")
            result.groups_skipped += 1
            continue
        if dry_run:
            print("  DRY RUN: no files written")
            continue
        try:
            _write_atomic(paths, target, overwrite=overwrite)
        except FileExistsError:
            print("  SKIPPED: destination was created by another process")
            result.groups_skipped += 1
        except Exception as exc:
            print(f"  ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
            result.groups_failed += 1
        else:
            print("  CREATED")
            result.groups_written += 1

    print(
        f"\nSummary: {result.groups_written} created; {result.groups_skipped} skipped; "
        f"{result.groups_failed} failed; {result.files_ignored} invalid-name PDF(s)."
    )
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Merge local PDF files by their YYYY-MM-DD- filename prefix. No network access."
    )
    parser.add_argument("--input-dir", type=Path, default=ROOT / "input", help="Source folder (default: ./input)")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "output", help="Destination folder (default: ./output)")
    parser.add_argument("--recursive", action="store_true", help="Include subfolders of input")
    parser.add_argument("--overwrite", action="store_true", help="Replace existing merged files")
    parser.add_argument("--dry-run", action="store_true", help="Preview groups without writing")
    args = parser.parse_args(argv)
    try:
        report = process(args.input_dir, args.output_dir, overwrite=args.overwrite, dry_run=args.dry_run, recursive=args.recursive)
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 1 if report.groups_failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

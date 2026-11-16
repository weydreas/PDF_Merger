"""Merge PDF documents by YYYY-MM-DD filename prefix, with optional AI preflight."""

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
    files_renamed: int = 0


def discover_groups(
    input_dir: Path, output_dir: Path, recursive: bool = False,
    name_overrides: dict[Path, str] | None = None,
) -> tuple[dict[str, list[Path]], list[Path]]:
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
        effective_stem = (name_overrides or {}).get(path, path.name)
        match = DATE_PREFIX.fullmatch(Path(effective_stem).stem)
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


def process(
    input_dir: Path, output_dir: Path, *, overwrite: bool = False,
    dry_run: bool = False, recursive: bool = False,
    rename_with_ai: bool = False, env_file: Path | None = None,
) -> Result:
    input_dir = input_dir.resolve()
    output_dir = output_dir.resolve()
    if not input_dir.is_dir():
        raise ValueError(f"Input directory does not exist: {input_dir}")
    if input_dir == output_dir:
        raise ValueError("Input and output directories must differ")

    name_overrides: dict[Path, str] = {}
    renamed = 0
    if rename_with_ai:
        from ai_renamer import apply_renames, plan_renames

        _, needs_renaming = discover_groups(input_dir, output_dir, recursive=recursive)
        if needs_renaming:
            # No external requests unless the user explicitly selects AI mode.
            # Finish every API request and validate every proposed date first.
            planned = plan_renames(needs_renaming, input_dir, env_file or ROOT / ".env")
            for original, proposed in planned.items():
                action = "ПЛАН AI" if dry_run else "AI-ПЕРЕИМЕНОВАНИЕ"
                print(f"{action}: {original.relative_to(input_dir)} -> {proposed.relative_to(input_dir)}")
            if dry_run:
                name_overrides = {old: new.name for old, new in planned.items()}
            else:
                apply_renames(planned)
                renamed = len(planned)

    groups, ignored = discover_groups(input_dir, output_dir, recursive=recursive, name_overrides=name_overrides)
    result = Result(files_ignored=len(ignored), groups_planned=len(groups), files_renamed=renamed)
    for path in ignored:
        print(f"ПРОПУЩЕН (нет корректной даты YYYY-MM-DD-): {path.relative_to(input_dir)}")
    if not groups:
        print("Не найдено PDF с корректным префиксом YYYY-MM-DD-.")
        return result

    if not dry_run:
        output_dir.mkdir(parents=True, exist_ok=True)

    for day, paths in groups.items():
        target = output_dir / f"{day}.pdf"
        print(f"\n{day}: {len(paths)} исходных PDF -> {target.name}")
        for index, source in enumerate(paths, start=1):
            print(f"  {index}. {source.relative_to(input_dir)}")
        if target.exists() and not overwrite:
            print("  ПРОПУЩЕНО: результат уже существует (для замены нужен --overwrite)")
            result.groups_skipped += 1
            continue
        if dry_run:
            print("  ПРОВЕРКА: файлы не записываются")
            continue
        try:
            _write_atomic(paths, target, overwrite=overwrite)
        except FileExistsError:
            print("  ПРОПУЩЕНО: результат уже создан другим процессом")
            result.groups_skipped += 1
        except Exception as exc:
            print(f"  ОШИБКА: {type(exc).__name__}: {exc}", file=sys.stderr)
            result.groups_failed += 1
        else:
            print("  СОЗДАНО")
            result.groups_written += 1

    print(
        f"\nИтог: {result.groups_written} создано; {result.groups_skipped} пропущено; "
        f"{result.groups_failed} ошибок; {result.files_ignored} PDF с некорректной датой; "
        f"{result.files_renamed} переименовано AI."
    )
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Merge local PDF files by their YYYY-MM-DD- prefix. Optional AI renaming sends PDF text excerpts to an external service."
    )
    parser.add_argument("--input-dir", type=Path, default=ROOT / "input", help="Source folder (default: ./input)")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "output", help="Destination folder (default: ./output)")
    parser.add_argument("--recursive", action="store_true", help="Include subfolders of input")
    parser.add_argument("--overwrite", action="store_true", help="Replace existing merged files")
    parser.add_argument("--dry-run", action="store_true", help="Preview groups without writing; AI mode may still send text to API")
    parser.add_argument("--rename-with-ai", action="store_true", help="Opt in to sending excerpts of wrongly named PDFs to external AI; rename them before merging")
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env", help="AI settings file (default: ./.env)")
    parser.add_argument("--check-names", action="store_true", help="Validate PDF names locally; returns 3 if AI preflight is needed")
    args = parser.parse_args(argv)
    try:
        if args.check_names:
            input_dir, output_dir = args.input_dir.resolve(), args.output_dir.resolve()
            if not input_dir.is_dir():
                raise ValueError(f"Input directory does not exist: {input_dir}")
            if input_dir == output_dir:
                raise ValueError("Input and output directories must differ")
            _, ignored = discover_groups(input_dir, output_dir, recursive=args.recursive)
            print(f"PDF с нестандартным именем или неверной датой: {len(ignored)}")
            return 3 if ignored else 0
        report = process(
            args.input_dir, args.output_dir, overwrite=args.overwrite,
            dry_run=args.dry_run, recursive=args.recursive,
            rename_with_ai=args.rename_with_ai, env_file=args.env_file,
        )
    except (OSError, ValueError) as exc:
        print(f"ОШИБКА: {exc}", file=sys.stderr)
        return 2
    return 1 if report.groups_failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

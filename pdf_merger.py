"""Merge PDF documents by YYYY-MM-DD filename prefix, with optional AI preflight."""

from __future__ import annotations

import argparse
import csv
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

from document_router import DocumentClass, classify_document


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
    ai_unresolved: int = 0


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


def plan_outputs(
    groups: dict[str, list[Path]], input_dir: Path, output_dir: Path,
    name_overrides: dict[Path, str] | None = None,
    classifications: dict[Path, DocumentClass] | None = None,
) -> list[tuple[Path, list[Path], str]]:
    """Lab results merge only with lab results for the same day.

    Each examination is its own PDF. Uncertain documents remain separate in
    На_проверку, never silently mixed with daily lab bundles.
    """
    labs: dict[str, list[Path]] = defaultdict(list)
    individual: list[tuple[str, Path, str, str]] = []
    for day, paths in groups.items():
        for path in paths:
            classification = (
                classifications.get(path) if classifications is not None else None
            )
            if classification is None:
                classification = classify_document(
                    path, effective_name=(name_overrides or {}).get(path, path.name)
                )
            if classification.category == "lab":
                labs[day].append(path)
            elif classification.category == "study":
                individual.append((day, path, "Обследования", classification.title))
            else:
                individual.append((day, path, "На_проверку", "Требует-проверки"))

    plans: list[tuple[Path, list[Path], str]] = []
    for day, paths in sorted(labs.items()):
        plans.append((output_dir / "Анализы" / f"{day}-Анализы.pdf", paths, "Анализы"))

    collisions: dict[tuple[str, str, str], int] = defaultdict(int)
    for day, path, folder, title in individual:
        key = (folder, day, title.casefold())
        collisions[key] += 1
        suffix = f"-{collisions[key]:02d}" if collisions[key] > 1 else ""
        target = output_dir / folder / f"{day}-{title}{suffix}.pdf"
        plans.append((target, [path], folder))
    return plans


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


def _write_membership_report(
    entries: list[tuple[Path, Path | None, str, str]],
    input_dir: Path,
    output_dir: Path,
) -> Path:
    """Write a local UTF-8 Excel-friendly audit; never claim skipped items merged.

    A PLANNED source of an existing result is NOT evidence that it was included.
    This report describes only the current run's confirmed contributions.
    """
    target = output_dir / "Состав_результатов.csv"
    output_dir.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8-sig", newline="",
            prefix=".membership-", suffix=".tmp", dir=output_dir,
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            writer = csv.writer(stream, delimiter=";")
            writer.writerow([
                "Исходный PDF", "Категория", "Итоговый файл",
                "Статус операции", "Подтверждено включение",
            ])
            for source, destination, category, status in entries:
                included = (
                    "ДА" if status == "СОЗДАНО"
                    else "НЕТ" if status == "НЕКОРРЕКТНАЯ_ДАТА"
                    else "НЕ ПОДТВЕРЖДЕНО"
                )
                writer.writerow([
                    str(source.relative_to(input_dir)), category,
                    str(destination.relative_to(output_dir)) if destination else "",
                    status, included,
                ])
        os.replace(temporary, target)
        temporary = None
        return target
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def process(
    input_dir: Path, output_dir: Path, *, overwrite: bool = False,
    dry_run: bool = False, recursive: bool = False,
    rename_with_ai: bool = False, env_file: Path | None = None,
    ai_classification: str | None = None,
) -> Result:
    input_dir = input_dir.resolve()
    output_dir = output_dir.resolve()
    if not input_dir.is_dir():
        raise ValueError(f"Input directory does not exist: {input_dir}")
    if input_dir == output_dir:
        raise ValueError("Input and output directories must differ")
    if ai_classification not in (None, "review", "all"):
        raise ValueError("Invalid AI classification mode")

    name_overrides: dict[Path, str] = {}
    renamed = 0
    unresolved = []
    if rename_with_ai:
        from ai_renamer import AIProgressLog, apply_renames, plan_renames

        _, needs_renaming = discover_groups(input_dir, output_dir, recursive=recursive)
        if needs_renaming:
            # No external requests unless the user explicitly selects AI mode.
            # Finish every API request and validate every proposed date first.
            progress = AIProgressLog(ROOT / "logs" / "ai-progress.log")
            progress(
                f"Начало: {len(needs_renaming)} PDF. Чтение и AI-запросы выполняются "
                "по очереди; до подтверждения всех дат имена не меняются."
            )
            try:
                planned = plan_renames(
                    needs_renaming, input_dir, env_file or ROOT / ".env",
                    progress=progress, unresolved=unresolved,
                )
            except (OSError, ValueError):
                progress("Проверка остановлена: планирование не завершено, исходные имена сохранены.")
                raise
            progress(
                f"AI-проверка завершена: {len(planned)} подтверждено, "
                f"{len(unresolved)} с недостоверной датой."
            )
            for issue in unresolved:
                # File names stay local in the console; the persistent progress
                # log retains only numeric indices and non-sensitive reason codes.
                print(
                    f"ТРЕБУЕТ ПРОВЕРКИ (документ {issue.number}/{len(needs_renaming)}): "
                    f"{issue.path.relative_to(input_dir)} — {issue.reason}. "
                    "Файл останется без изменений.",
                    flush=True,
                )
            for original, proposed in planned.items():
                action = "ПЛАН AI" if dry_run else "AI-ПЕРЕИМЕНОВАНИЕ"
                print(f"{action}: {original.relative_to(input_dir)} -> {proposed.relative_to(input_dir)}")
            if dry_run:
                progress("Режим проверки: файловая система не менялась.")
                name_overrides = {old: new.name for old, new in planned.items()}
            else:
                progress(f"Начало изменения имён {len(planned)} файлов в input.")
                try:
                    apply_renames(planned)
                except (OSError, ValueError):
                    progress("ОШИБКА файловой операции. Проверьте input: часть имён могла измениться.")
                    raise
                renamed = len(planned)
                progress(
                    f"Переименование завершено: {renamed} файлов. "
                    + ("Итоговые PDF отложены из-за неподтверждённых дат." if unresolved else "Переход к объединению.")
                )

    groups, ignored = discover_groups(input_dir, output_dir, recursive=recursive, name_overrides=name_overrides)
    classifications: dict[Path, DocumentClass] = {}
    if ai_classification and not (unresolved and not dry_run):
        from ai_renamer import AIProgressLog
        from semantic_classifier import classify_documents_with_ai

        all_paths = [path for paths in groups.values() for path in paths]
        if ai_classification == "review":
            ai_paths = [
                path for path in all_paths
                if classify_document(
                    path, effective_name=name_overrides.get(path, path.name)
                ).category == "review"
            ]
        else:
            ai_paths = all_paths
        print(
            f"СЕМАНТИЧЕСКАЯ AI-КЛАССИФИКАЦИЯ: {len(ai_paths)} из "
            f"{len(all_paths)} PDF; режим {ai_classification}. "
            "Текст документа передаётся во внешний API только по вашему выбору."
        )
        if ai_paths:
            progress = AIProgressLog(ROOT / "logs" / "ai-progress.log")
            classifications = classify_documents_with_ai(
                ai_paths, env_file or ROOT / ".env",
                ROOT / "logs" / "ai-classification-cache.json",
                progress=progress,
            )
    plans = plan_outputs(
        groups, input_dir, output_dir, name_overrides=name_overrides,
        classifications=classifications,
    )
    result = Result(
        files_ignored=len(ignored), groups_planned=len(plans),
        files_renamed=renamed, ai_unresolved=len(unresolved),
    )
    report_entries: list[tuple[Path, Path | None, str, str]] = [
        (path, None, "Не обработано", "НЕКОРРЕКТНАЯ_ДАТА")
        for path in ignored
    ]
    for path in ignored:
        print(f"ПРОПУЩЕН (нет корректной даты YYYY-MM-DD-): {path.relative_to(input_dir)}")
    if unresolved and not dry_run:
        print(
            "\nНЕПОЛНАЯ AI-ПРОВЕРКА: документы с подтверждённой датой переименованы, "
            "но создание итоговых PDF отложено до проверки оставшихся файлов."
        )
        print(
            "При следующем запуске AI потребуется только для файлов без "
            "правильного YYYY-MM-DD- имени. Исходные PDF и прежний output сохранены."
        )
        return result

    if not plans:
        print("Не найдено документов с корректной датой для обработки.")
        return result

    # Legacy mixed outputs in the root are preserved, never read as source or
    # treated as proof that new structured outputs were already produced.
    if output_dir.is_dir() and any(
        re.fullmatch(r"\d{4}-\d{2}-\d{2}\.pdf", p.name) for p in output_dir.iterdir() if p.is_file()
    ):
        print("ВНИМАНИЕ: старые объединённые PDF в корне output сохранены без изменений.")
        print("Новые раздельные результаты будут записаны в подпапки Анализы, Обследования, На_проверку.")

    for target, paths, category in plans:
        print(f"\n{category}: {len(paths)} PDF -> {target.relative_to(output_dir)}")
        for index, source in enumerate(paths, start=1):
            print(f"  {index}. {source.relative_to(input_dir)}")
        if target.exists() and not overwrite:
            print(
                "  ПРОПУЩЕНО: результат уже существует и НЕ ОБНОВЛЁН. "
                "Перечисленные выше PDF — только план; они НЕ обязательно входят "
                "в существующий файл. Для пересборки нужен --overwrite."
            )
            report_entries.extend(
                (source, target, category, "ПРОПУЩЕНО_УЖЕ_СУЩЕСТВУЕТ")
                for source in paths
            )
            result.groups_skipped += 1
            continue
        if dry_run:
            print("  ПРОВЕРКА: файлы не записываются")
            continue
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            _write_atomic(paths, target, overwrite=overwrite)
        except FileExistsError:
            print("  ПРОПУЩЕНО: результат уже создан другим процессом")
            report_entries.extend(
                (source, target, category, "ПРОПУЩЕНО_УЖЕ_СУЩЕСТВУЕТ")
                for source in paths
            )
            result.groups_skipped += 1
        except Exception as exc:
            print(f"  ОШИБКА: {type(exc).__name__}: {exc}", file=sys.stderr)
            report_entries.extend(
                (source, target, category, "ОШИБКА")
                for source in paths
            )
            result.groups_failed += 1
        else:
            print("  СОЗДАНО")
            report_entries.extend(
                (source, target, category, "СОЗДАНО")
                for source in paths
            )
            result.groups_written += 1

    if not dry_run:
        try:
            report_path = _write_membership_report(
                report_entries, input_dir, output_dir
            )
        except OSError as exc:
            # A nonessential CSV export cannot interrupt PDF creation.
            print(
                f"ПРЕДУПРЕЖДЕНИЕ: отчёт о составе не записан: {type(exc).__name__}",
                file=sys.stderr,
            )
        else:
            print(
                f"\nПРОВЕРКА СОСТАВА: {report_path.relative_to(output_dir)}. "
                "Включение подтверждено ТОЛЬКО для строк со статусом СОЗДАНО."
            )
    old_review = output_dir / "На_проверку"
    if old_review.is_dir() and any(p.is_file() and p.suffix.lower() == ".pdf" for p in old_review.iterdir()):
        print(
            "ПРИМЕЧАНИЕ: в output/На_проверку остаются PDF прежних запусков. "
            "Они НЕ удаляются автоматически и могут быть устаревшими. "
            "Актуальное включение документов проверяйте в Состав_результатов.csv."
        )
    if result.groups_skipped:
        print(
            "ВНИМАНИЕ: существующие результаты не обновлены. "
            "Их состав НЕ проверен по новому плану — используйте --overwrite "
            "после проверки исходных файлов."
        )

    print(
        f"\nИтог: {result.groups_written} создано; {result.groups_skipped} пропущено; "
        f"{result.groups_failed} ошибок; {result.files_ignored} PDF с некорректной датой; "
        f"{result.files_renamed} переименовано AI; "
        f"{result.ai_unresolved} требуют проверки даты."
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
    parser.add_argument(
        "--classify-with-ai", choices=("review", "all"),
        help="Explicitly authorize external AI to classify review-only or all dated PDFs. May send medical text and incur API charges; uses local hash cache.",
    )
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
            ai_classification=args.classify_with_ai,
        )
    except (OSError, ValueError) as exc:
        print(f"ОШИБКА: {exc}", file=sys.stderr)
        return 2
    return 1 if (report.groups_failed or report.ai_unresolved) else 0


if __name__ == "__main__":
    raise SystemExit(main())

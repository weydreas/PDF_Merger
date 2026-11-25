"""Tests with synthetic blank PDFs only; no medical information."""

from contextlib import redirect_stderr, redirect_stdout
import csv
from io import StringIO
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from pypdf import PdfReader, PdfWriter

from pdf_merger import discover_groups, main, process
from document_router import DocumentClass, classify_text


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
        out, err = StringIO(), StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            result = process(self.input, self.output, **kwargs)
        self.last_run_output = out.getvalue() + "\n" + err.getvalue()
        return result

    def read_membership_report(self):
        path = self.output / "Состав_результатов.csv"
        with path.open(encoding="utf-8-sig", newline="") as handle:
            return list(csv.DictReader(handle, delimiter=";"))

    def run_as_labs(self, **kwargs):
        with patch(
            'pdf_merger.classify_document',
            return_value=DocumentClass('lab', 'Анализы', 'synthetic fixture'),
        ):
            return self.run_process(**kwargs)

    def test_groups_pages_sorted_and_preserved(self):
        make_pdf(self.input / "2024-05-29-Бета.pdf", [202, 203])
        make_pdf(self.input / "2024-05-29-Альфа.PDF", [101])
        make_pdf(self.input / "2024-05-30-Иное.pdf", [301])
        report = self.run_as_labs()
        self.assertEqual(report.groups_written, 2)
        self.assertEqual(
            sorted(x.name for x in (self.output / "Анализы").iterdir()),
            ["2024.05.29-Анализы.pdf", "2024.05.30-Анализы.pdf"],
        )
        reader = PdfReader(self.output / "Анализы" / "2024.05.29-Анализы.pdf")
        self.assertEqual([float(p.mediabox.width) for p in reader.pages], [101, 202, 203])

    def test_single_file_keeps_identical_bytes(self):
        path = self.input / "2026-01-01-only.pdf"
        make_pdf(path, [103])
        self.run_as_labs()
        self.assertEqual((self.output / "Анализы" / "2026.01.01-Анализы.pdf").read_bytes(), path.read_bytes())

    def test_skips_existing_then_explicit_overwrite(self):
        file1 = self.input / "2025-02-13-first.pdf"
        make_pdf(file1, [111])
        self.run_as_labs()
        file2 = self.input / "2025-02-13-second.pdf"
        make_pdf(file2, [222])
        r = self.run_as_labs()
        self.assertEqual((r.groups_written, r.groups_skipped), (0, 1))
        self.assertEqual(len(PdfReader(self.output / "Анализы" / "2025.02.13-Анализы.pdf").pages), 1)
        r = self.run_as_labs(overwrite=True)
        self.assertEqual(r.groups_written, 1)
        self.assertEqual(len(PdfReader(self.output / "Анализы" / "2025.02.13-Анализы.pdf").pages), 2)

    def test_dry_run_changes_nothing(self):
        make_pdf(self.input / "2024-02-29-leap.pdf", [120])
        self.run_process(dry_run=True)
        self.assertFalse(self.output.exists())

    def test_invalid_dates_and_unnamed_are_ignored(self):
        make_pdf(self.input / "2025-02-29-invalid.pdf", [130])
        make_pdf(self.input / "no-prefix.pdf", [140])
        make_pdf(self.input / "2024-02-29-valid.pdf", [150])
        r = self.run_as_labs()
        self.assertEqual(r.files_ignored, 2)
        self.assertEqual(r.groups_written, 1)

    def test_corrupt_group_cannot_publish_partial_output(self):
        make_pdf(self.input / "2024-01-01-first.pdf", [101])
        (self.input / "2024-01-01-broken.pdf").write_bytes(b"not a pdf")
        make_pdf(self.input / "2024-01-02-ok.pdf", [102])
        r = self.run_as_labs()
        self.assertEqual((r.groups_failed, r.groups_written), (1, 1))
        self.assertFalse((self.output / "Анализы" / "2024.01.01-Анализы.pdf").exists())
        self.assertTrue((self.output / "Анализы" / "2024.01.02-Анализы.pdf").exists())
        self.assertEqual(list(self.output.glob("*.tmp")), [])

    def test_recursive_excludes_output_subfolder(self):
        out = self.input / "output"
        make_pdf(self.input / "sub" / "2024-08-01-a.pdf", [111])
        make_pdf(out / "2024-08-01-old.pdf", [222])
        with redirect_stdout(StringIO()), patch(
            'pdf_merger.classify_document',
            return_value=DocumentClass('lab', 'Анализы', 'synthetic fixture'),
        ):
            r = process(self.input, out, recursive=True)
        self.assertEqual(r.groups_written, 1)
        self.assertEqual(len(PdfReader(out / "Анализы" / "2024.08.01-Анализы.pdf").pages), 1)

    def test_real_text_classification_prefers_xray_to_generic_tests(self):
        study = classify_text(
            "2026-05-19-Tests.pdf",
            "Рентгенография органов грудной клетки. Заключение: без патологий.",
        )
        self.assertEqual(study.category, "study")
        self.assertEqual(study.title, "Рентгенография грудной клетки")
        self.assertEqual(classify_text("2026-05-19-Test.pdf", "").category, "review")
        labs = classify_text("2026-05-19-Tests.pdf", "Клинический анализ крови ОАК. Гемоглобин")
        self.assertEqual(labs.category, "lab")
        self.assertEqual(labs.title, "Общий анализ крови")

    def test_export_header_does_not_turn_xray_into_lab(self):
        # Mock text, never a real patient report. The exporter uses the word
        # "лабораторного" in EVERY document's generic registration field.
        sample = (
            "Дата назначения лабораторного исследования: 17.11.2026\n"
            "Исследование Результат Единицы измерения\n"
            "7002892 - Рентгенография грудной клетки. Врач: И.И.\n"
            "Описание\nРентгенография грудной клетки.\n"
            "Заключение\nРекомендовано УЗИ для дальнейшей оценки.\n"
        )
        result = classify_text("2026.11.17-Требует-проверки.pdf", sample)
        self.assertEqual((result.category, result.title),
                         ("study", "Рентгенография грудной клетки"))

    def test_ecg_mentioned_after_generic_lab_header_is_an_exam(self):
        sample = (
            "Дата назначения лабораторного исследования: 18.11.2026\n"
            + "Примечание.\n" * 65
            + "Исследование Результат Единицы измерения\n"
            + "7014145 - Электрокардиография. Врач: И.И.\n"
            + "Заключение\nЭлектрокардиография: ритм синусовый.\n"
        )
        result = classify_text("2026.11.21-Требует-проверки.pdf", sample)
        self.assertEqual((result.category, result.title), ("study", "ЭКГ"))

    def test_study_names_are_short_and_based_on_performed_procedure(self):
        for procedure, expected in (
            ("Компьютерная томография грудной клетки", "КТ грудной клетки"),
            ("Магнитно-резонансная томография головного мозга", "МРТ головного мозга"),
            ("УЗИ щитовидной железы", "УЗИ щитовидной железы"),
        ):
            with self.subTest(procedure=procedure):
                sample = (
                    "Дата назначения лабораторного исследования: 10.11.2026\n"
                    "7002892 - " + procedure + ". Врач: И.И.\n"
                )
                got = classify_text("2026.11.10-Требует-проверки.pdf", sample)
                self.assertEqual((got.category, got.title), ("study", expected))

    def test_lab_codes_are_labs_not_generic_export_headers(self):
        sample = (
            "Дата назначения лабораторного исследования: 17.11.2026\n"
            "B03.016.002 - Общий анализ крови. Врач: И.И.\n"
            "A09.05.042 - Аланинаминотрансфераза. Врач: И.И.\n"
            "Лейкоциты WBC 5.0 10Е9/л\n"
        )
        self.assertEqual(classify_text("2026.11.17-Требует-проверки.pdf", sample).category, "lab")

    def test_mixed_procedure_rows_and_generic_header_require_review(self):
        for sample in (
            "Дата назначения лабораторного исследования\n"
            "7002892 - Рентгенография грудной клетки. Врач: И.И.\n"
            "7014145 - Электрокардиография. Врач: И.И.\n",
            "B03.016.002 - Общий анализ крови. Врач: И.И.\n"
            "7002892 - Рентгенография грудной клетки. Врач: И.И.\n",
            "Дата назначения лабораторного исследования: 10.11.2026\n",
            "A04.01.002 - Неизвестная диагностическая процедура. Врач: И.И.\n",
        ):
            with self.subTest(case=sample[:28]):
                self.assertEqual(classify_text("2026-11-10-Тесты.pdf", sample).category, "review")

    def test_synthetic_export_routing_keeps_five_exams_separate_without_ai(self):
        texts = {
            "2026.11.17-Требует-проверки.pdf":
                "Дата назначения лабораторного исследования\n"
                "7002892 - Рентгенография грудной клетки. Врач: И.И.\n",
            "2026.11.17-Требует-проверки-02.pdf":
                "Дата назначения лабораторного исследования\n"
                "7014145 - Электрокардиография. Врач: И.И.\n",
            "2026.11.17-Требует-проверки-03.pdf":
                "Дата назначения лабораторного исследования\n"
                "7002892 - Рентгенография грудной клетки. Врач: И.И.\n",
            "2026.11.18-Требует-проверки.pdf":
                "Дата назначения лабораторного исследования\n"
                "7002892 - Рентгенография грудной клетки. Врач: И.И.\n"
                "Заключение: рекомендовано УЗИ плевральных полостей.\n",
            "2026.11.21-Требует-проверки.pdf":
                "Дата назначения лабораторного исследования\n"
                "7014145 - Электрокардиография. Врач: И.И.\n",
            "2026-11-17-ОАК.pdf":
                "B03.016.002 - Общий анализ крови. Врач: И.И.\n",
        }
        for index, filename in enumerate(texts, start=1):
            make_pdf(self.input / filename, [100 + index])

        def classify(path, effective_name=None):
            return classify_text(effective_name or path.name, texts[path.name])

        with patch("pdf_merger.classify_document", side_effect=classify):
            result = self.run_process()
        self.assertEqual(result.groups_written, 6, self.last_run_output)
        self.assertEqual(result.groups_failed, 0, self.last_run_output)
        studies = sorted((self.output / "Обследования").glob("*.pdf"))
        self.assertEqual(len(studies), 5)
        self.assertEqual(
            sorted(x.name for x in studies),
            [
                "2026.11.17-Рентгенография грудной клетки-02.pdf",
                "2026.11.17-Рентгенография грудной клетки.pdf",
                "2026.11.17-ЭКГ.pdf",
                "2026.11.18-Рентгенография грудной клетки.pdf",
                "2026.11.21-ЭКГ.pdf",
            ],
        )
        self.assertEqual(
            len(list((self.output / "Анализы").glob("*.pdf"))), 1,
        )
        self.assertFalse((self.output / "На_проверку").exists())

    def test_reference_value_ranges_never_become_procedure_codes(self):
        # A previous release parsed '110.0 - 160.0' as a medical procedure.
        # These are synthetic ranges, not real patient values.
        examples = (
            "B03.016.002 - Общий анализ крови. Врач: И.И.\n"
            "Гемоглобин 120 г/л 110.0 - 160.0\n"
            "MCHC 345 г/л 320.0 - 360.0\n"
            "Тромбоциты 250 10Е9/л 150.0 - 400.0\n",
            "A09.05.209 - Прокальцитонин. Врач: И.И.\n"
            "A09.05.042 - АЛТ. Врач: И.И.\n"
            "ЛДГ 250.0 Ед/л 110.0 - 295.0\n"
            "Натрий 138 ммоль/л 135.0 - 145.0\n",
            "B03.016.011 - Кислотно-основное состояние крови. Врач: И.И.\n"
            "Натрий 140 ммоль/л 135.0 - 145.0\n",
            "B03.016.003 - Общий анализ крови развернутый. Врач: И.И.\n"
            "Гемоглобин 122 г/л 114.0 - 134.0\n"
            "MCHC 340 г/л 320.0 - 360.0\n"
            "Тромбоциты 230 10Е9/л 187.0 - 445.0\n",
        )
        for text in examples:
            with self.subTest(text=text[:20]):
                result = classify_text("2026.11.18-Требует-проверки.pdf", text)
                self.assertEqual(result.category, "lab", result)

    def test_numeric_procedure_ids_still_route_to_examinations(self):
        document = (
            "Дата назначения лабораторного исследования: 18.11.2026\n"
            "7002892 - Рентгенография грудной клетки. Врач: И.И.\n"
            "Гемоглобин 125 г/л 110.0 - 160.0\n"
        )
        result = classify_text("2026.11.18-Требует-проверки.pdf", document)
        self.assertEqual((result.category, result.title),
                         ("study", "Рентгенография грудной клетки"))

    def test_membership_report_marks_created_only_when_confirmed(self):
        first = self.input / "2026.11.17-Требует-проверки.pdf"
        second = self.input / "2026.11.17-Требует-проверки-02.pdf"
        make_pdf(first, [111])
        with patch(
            "pdf_merger.classify_document",
            return_value=DocumentClass("lab", "Общий анализ крови", "evidenced"),
        ):
            initial = self.run_process()
        self.assertEqual(initial.groups_written, 1, self.last_run_output)
        make_pdf(second, [222])
        with patch(
            "pdf_merger.classify_document",
            return_value=DocumentClass("lab", "Общий анализ крови", "evidenced"),
        ):
            skipped = self.run_process()
        self.assertEqual(skipped.groups_skipped, 1, self.last_run_output)
        rows = self.read_membership_report()
        self.assertEqual(len(rows), 2)
        self.assertEqual({row["Исходный PDF"] for row in rows}, {first.name, second.name})
        self.assertTrue(all(row["Подтверждено включение"] == "НЕ ПОДТВЕРЖДЕНО" for row in rows))
        self.assertTrue(all(row["Статус операции"] == "ПРОПУЩЕНО_УЖЕ_СУЩЕСТВУЕТ" for row in rows))
        self.assertIn("НЕ ОБНОВЛЁН", self.last_run_output)
        self.assertEqual(len(PdfReader(self.output / "Анализы" / "2026.11.17-Анализы.pdf").pages), 1)

        with patch(
            "pdf_merger.classify_document",
            return_value=DocumentClass("lab", "Общий анализ крови", "evidenced"),
        ):
            updated = self.run_process(overwrite=True)
        self.assertEqual(updated.groups_written, 1, self.last_run_output)
        rows = self.read_membership_report()
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(row["Подтверждено включение"] == "ДА" for row in rows))
        self.assertTrue(all(row["Статус операции"] == "СОЗДАНО" for row in rows))
        self.assertEqual(
            sorted(int(page.mediabox.width) for page in PdfReader(
                self.output / "Анализы" / "2026.11.17-Анализы.pdf").pages),
            [111, 222],
        )
        self.assertEqual(first.read_bytes(), (self.input / first.name).read_bytes())

    def test_report_separates_review_from_created_labs(self):
        lab = self.input / "2026-11-18-Биохимия.pdf"
        unknown = self.input / "2026-11-18-Неизвестно.pdf"
        ignored = self.input / "no-date.pdf"
        for path, width in [(lab, 101), (unknown, 102), (ignored, 103)]:
            make_pdf(path, [width])
        def categorize(path, effective_name=None):
            return (
                DocumentClass("lab", "Биохимия", "evidenced")
                if path.name == lab.name
                else DocumentClass("review", "Требует-проверки", "not evidenced")
            )
        with patch("pdf_merger.classify_document", side_effect=categorize):
            result = self.run_process()
        self.assertEqual((result.groups_written, result.files_ignored), (2, 1))
        rows = {row["Исходный PDF"]: row for row in self.read_membership_report()}
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[lab.name]["Категория"], "Анализы")
        self.assertIn("Анализы", rows[lab.name]["Итоговый файл"])
        self.assertEqual(rows[unknown.name]["Категория"], "На_проверку")
        self.assertIn("На_проверку", rows[unknown.name]["Итоговый файл"])
        self.assertEqual(rows[ignored.name]["Подтверждено включение"], "НЕТ")
        self.assertEqual(rows[ignored.name]["Статус операции"], "НЕКОРРЕКТНАЯ_ДАТА")

    def test_dry_run_never_writes_membership_report(self):
        make_pdf(self.input / "2026-11-17-ОАК.pdf", [104])
        with patch(
            "pdf_merger.classify_document",
            return_value=DocumentClass("lab", "ОАК", "evidenced"),
        ):
            self.run_process(dry_run=True)
        self.assertFalse(self.output.exists())

    def test_unwritable_membership_report_does_not_break_merging(self):
        make_pdf(self.input / "2026-11-17-ОАК.pdf", [105])
        with patch(
            "pdf_merger.classify_document",
            return_value=DocumentClass("lab", "ОАК", "evidenced"),
        ), patch("pdf_merger._write_membership_report", side_effect=PermissionError("denied")):
            report = self.run_process()
        self.assertEqual(report.groups_written, 1, self.last_run_output)
        self.assertTrue((self.output / "Анализы" / "2026.11.17-Анализы.pdf").exists())
        self.assertIn("отчёт о составе не записан", self.last_run_output)

    def test_lab_and_study_on_same_date_never_merge(self):
        lab1 = self.input / "2026-05-19-Биохимия.pdf"
        lab2 = self.input / "2026-05-19-ОАК.pdf"
        xray = self.input / "2026-05-19-Тесты.pdf"
        for path, width in ((lab1, 111), (lab2, 222), (xray, 333)):
            make_pdf(path, [width])
        def classification(path, effective_name=None):
            if path.name == xray.name:
                return DocumentClass("study", "Рентгенография грудной клетки", "verified text")
            return DocumentClass("lab", "Анализы", "verified lab markers")
        with patch("pdf_merger.classify_document", side_effect=classification):
            result = self.run_process()
        self.assertEqual(result.groups_written, 2, self.last_run_output)
        labs = self.output / "Анализы" / "2026.05.19-Анализы.pdf"
        imaging = self.output / "Обследования" / "2026.05.19-Рентгенография грудной клетки.pdf"
        self.assertEqual([int(x.mediabox.width) for x in PdfReader(labs).pages], [111, 222])
        self.assertEqual(imaging.read_bytes(), xray.read_bytes())
        self.assertEqual(len(PdfReader(imaging).pages), 1)

    def test_two_same_day_studies_remain_separate_and_keep_unique_names(self):
        first = self.input / "2026-05-19-Study-1.pdf"
        second = self.input / "2026-05-19-Study-2.pdf"
        make_pdf(first, [111])
        make_pdf(second, [222])
        with patch(
            "pdf_merger.classify_document",
            return_value=DocumentClass("study", "Рентгенография", "verified"),
        ):
            result = self.run_process()
        self.assertEqual(result.groups_written, 2)
        folder = self.output / "Обследования"
        self.assertEqual(
            sorted(x.name for x in folder.iterdir()),
            ["2026.05.19-Рентгенография-02.pdf", "2026.05.19-Рентгенография.pdf"],
        )
        self.assertEqual(
            (folder / "2026.05.19-Рентгенография-02.pdf").read_bytes(),
            second.read_bytes(),
        )

    def test_unclassified_docs_are_not_merged_into_labs(self):
        unknown = self.input / "2026-05-19-Тест.pdf"
        lab = self.input / "2026-05-19-ОАК.pdf"
        make_pdf(unknown, [501])
        make_pdf(lab, [502])
        def classifier(path, effective_name=None):
            if path.name == unknown.name:
                return DocumentClass("review", "Требует-проверки", "uncertain")
            return DocumentClass("lab", "ОАК", "evidenced")
        with patch("pdf_merger.classify_document", side_effect=classifier):
            r = self.run_process()
        self.assertEqual(r.groups_written, 2, self.last_run_output)
        self.assertEqual(
            (self.output / "На_проверку" / "2026.05.19-Требует-проверки.pdf").read_bytes(),
            unknown.read_bytes(),
        )
        self.assertEqual(
            (self.output / "Анализы" / "2026.05.19-Анализы.pdf").read_bytes(),
            lab.read_bytes(),
        )

    def test_legacy_mixed_outputs_preserved_and_new_outputs_separate(self):
        make_pdf(self.output / "2026-05-19.pdf", [999])
        make_pdf(self.input / "2026-05-19-ОАК.pdf", [111])
        with patch(
            "pdf_merger.classify_document",
            return_value=DocumentClass("lab", "ОАК", "evidenced"),
        ):
            r = self.run_process()
        self.assertEqual(r.groups_written, 1)
        self.assertEqual(
            [int(p.mediabox.width) for p in PdfReader(self.output / "2026-05-19.pdf").pages],
            [999],
        )
        self.assertTrue((self.output / "Анализы" / "2026.05.19-Анализы.pdf").exists())

    def test_input_output_same_rejected(self):
        with self.assertRaisesRegex(ValueError, "must differ"):
            process(self.input, self.input)

    def test_cli_returns_nonzero_for_bad_input(self):
        with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            self.assertEqual(main(["--input-dir", str(self.root / "absent")]), 2)


if __name__ == "__main__":
    unittest.main()

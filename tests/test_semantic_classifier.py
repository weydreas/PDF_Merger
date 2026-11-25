"""Synthetic semantic AI routing tests; never send a medical document/API call."""

from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from pypdf import PdfWriter

from document_router import DocumentClass
from pdf_merger import process
from semantic_classifier import (
    _minimal_excerpt, _sanitize_classification, classify_documents_with_ai,
)


def make_pdf(path: Path, width: float = 100):
    path.parent.mkdir(parents=True, exist_ok=True)
    writer = PdfWriter()
    writer.add_blank_page(width=width, height=100)
    with path.open("wb") as output:
        writer.write(output)
    writer.close()


class SemanticClassifierTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.input = self.root / "input"
        self.output = self.root / "output"
        self.input.mkdir()
        self.env = self.root / ".env"
        self.env.write_text(
            "PDF_MERGER_API_KEY=testing-only-dummy-value\n"
            "PDF_MERGER_AI_MODEL=gpt-6-luna\n",
            encoding="utf-8",
        )
        self.cache = self.root / "logs" / "ai-classification-cache.json"
        self.first = self.input / "2026.11.18-Требует-проверки.pdf"
        self.second = self.input / "2026.11.18-Требует-проверки-02.pdf"
        make_pdf(self.first, width=151)
        make_pdf(self.second, width=252)

    def test_semantic_lab_is_not_confused_by_reference_ranges(self):
        excerpt = (
            "Исследование Результат Единицы\n"
            "B03.016.002 - Общий анализ крови. Врач: Т.Т.\n"
            "Гемоглобин 123 г/л 110.0 - 160.0\n"
            "Калий 4.2 ммоль/л 3.5 - 5.1\n"
        )
        evidence = "B03.016.002 - Общий анализ крови."
        answer = {"category": "lab", "title": "Анализы", "evidence": evidence}
        got = _sanitize_classification(
            answer, excerpt,
            DocumentClass("review", "Требует-проверки", "неоднозначно"),
        )
        self.assertEqual((got.category, got.title), ("lab", "Анализы"))

    def test_semantic_study_ignores_only_recommended_ultrasound(self):
        excerpt = (
            "7002892 - Рентгенография грудной клетки. Врач: Т.Т.\n"
            "Рекомендовано УЗИ плевральных полостей.\n"
        )
        answer = {
            "category": "study", "title": "Рентгенография грудной клетки",
            "evidence": "7002892 - Рентгенография грудной клетки.",
        }
        got = _sanitize_classification(
            answer, excerpt,
            DocumentClass("study", "Рентгенография грудной клетки", "text"),
        )
        self.assertEqual((got.category, got.title),
                         ("study", "Рентгенография грудной клетки"))

    def test_single_short_analyte_is_sufficient(self):
        for evidence in ("Глюкоза", "ТТГ", "Калий\n4.2 ммоль/л", ""):
            with self.subTest(evidence=evidence):
                result = _sanitize_classification(
                    {"category": "lab", "title": "Анализы", "evidence": evidence},
                    evidence,
                    DocumentClass("review", "Требует-проверки", "неоднозначный набор проведённых процедур"),
                )
                self.assertEqual(result.category, "lab")

    def test_model_decision_is_not_vetoed_by_local_modality(self):
        result = _sanitize_classification(
            {"category": "lab", "title": "Анализы", "evidence": "Глюкоза"},
            "Глюкоза 5.2. Рекомендовано УЗИ.",
            DocumentClass("study", "УЗИ", "local heuristic"),
        )
        self.assertEqual(result.category, "lab")

    def test_model_can_choose_review_for_genuinely_mixed_documents(self):
        result = _sanitize_classification(
            {"category": "review", "title": "Требует-проверки", "evidence": ""},
            "ЭКГ и анализ крови фактически выполнены.",
            DocumentClass("lab", "Анализы", "local heuristic"),
        )
        self.assertEqual(result.category, "review")

    def test_study_outside_previous_whitelist_is_accepted(self):
        result = _sanitize_classification(
            {"category": "study", "title": "Ангиоскан", "evidence": ""},
            "Ангиоскан",
            DocumentClass("review", "Требует-проверки", "unknown"),
        )
        self.assertEqual((result.category, result.title), ("study", "Ангиоскан"))

    def test_malformed_response_and_unsafe_filename_still_require_review(self):
        for answer in (None, {}, {"category": "unknown"}, {"category": "study", "title": "../outside"}, {"category": "study", "title": "a\\\\b"}, {"category": "study", "title": ""}):
            with self.subTest(answer=answer):
                self.assertEqual(_sanitize_classification(answer, "").category, "review")

    def test_prompt_change_invalidates_cached_decision(self):
        answer = {"category": "lab", "title": "Анализы", "evidence": "Глюкоза"}
        with patch("semantic_classifier.extract_excerpt", return_value="Глюкоза 5.2"), patch("semantic_classifier.request_classification", return_value=answer) as api:
            classify_documents_with_ai([self.first], self.env, self.cache)
            with patch("semantic_classifier.INSTRUCTIONS", "Changed instructions"):
                classify_documents_with_ai([self.first], self.env, self.cache)
        self.assertEqual(api.call_count, 2)

    def test_review_is_retried_instead_of_reusing_cached_rejection(self):
        with patch("semantic_classifier.extract_excerpt", return_value="Глюкоза 5.2"), patch("semantic_classifier.request_classification", side_effect=[{"category": "review"}, {"category": "lab", "title": "Анализы"}]) as api:
            first = classify_documents_with_ai([self.first], self.env, self.cache)
            second = classify_documents_with_ai([self.first], self.env, self.cache)
        self.assertEqual(api.call_count, 2)
        self.assertEqual(first[self.first].category, "review")
        self.assertEqual(second[self.first].category, "lab")

    def test_reference_filter_keeps_studies_and_originals_byte_identical(self):
        from pypdf import PdfReader
        for path in (self.first, self.second):
            writer = PdfWriter()
            for width in (111, 222, 333):
                writer.add_blank_page(width=width, height=100)
            with path.open("wb") as handle:
                writer.write(handle)
            writer.close()
        originals = {path: path.read_bytes() for path in (self.first, self.second)}
        decisions = {
            self.first: DocumentClass("lab", "Анализы", "AI"),
            self.second: DocumentClass("study", "Ангиоскан", "AI"),
        }
        with patch("pdf_merger.ROOT", self.root), patch(
            "semantic_classifier.classify_documents_with_ai", return_value=decisions
        ), patch("semantic_classifier.select_lab_pages", return_value=(0, 2)) as filter_pages, redirect_stdout(StringIO()):
            result = process(self.input, self.output, ai_classification="all", env_file=self.env,
                             filter_lab_reference_pages=True)
        self.assertEqual(result.groups_written, 2)
        filter_pages.assert_called_once()
        self.assertEqual(filter_pages.call_args.args[0], self.first)
        self.assertEqual([int(p.mediabox.width) for p in PdfReader(self.output / "Анализы" / "2026.11.18-Анализы.pdf").pages], [111, 333])
        self.assertEqual((self.output / "Обследования" / "2026.11.18-Ангиоскан.pdf").read_bytes(), originals[self.second])
        for path, data in originals.items():
            self.assertEqual(path.read_bytes(), data)

    def test_page_filter_keeps_unrecognized_pages_and_requires_result(self):
        from semantic_classifier import select_lab_pages
        from ai_renamer import load_config
        from unittest.mock import MagicMock
        pages = []
        for content in ("Глюкоза 5.2", "Общая справка", "Клинический комментарий"):
            page = MagicMock()
            page.extract_text.return_value = content
            page.images = []
            pages.append(page)
        reader = MagicMock()
        reader.pages = pages
        reader.is_encrypted = False
        cfg = load_config(self.env)
        with patch("pypdf.PdfReader", return_value=reader), patch(
            "semantic_classifier.request_classification",
            side_effect=[{"category": "result"}, {"category": "reference_only"}, {"category": "retain"}]
        ):
            self.assertEqual(select_lab_pages(self.first, cfg), (0, 2))
        with patch("pypdf.PdfReader", return_value=reader), patch(
            "semantic_classifier.request_classification",
            return_value={"category": "reference_only"}
        ):
            self.assertIsNone(select_lab_pages(self.first, cfg))

    def test_page_filter_requires_explicit_ai_consent(self):
        with self.assertRaises(ValueError):
            process(self.input, self.output, filter_lab_reference_pages=True)

    def test_pii_header_can_be_trimmed_from_lis_export(self):
        doc = (
            "ФИО пациента: Скрыто Имя\nАдрес: скрыто\n"
            "Исследование Результат Единицы\n"
            "B03.016.002 - Общий анализ крови. Врач: Т.Т.\n"
        )
        cleaned = _minimal_excerpt(doc)
        self.assertNotIn("ФИО пациента", cleaned)
        self.assertIn("B03.016.002", cleaned)

    def test_cache_prevents_repeated_paid_request_for_unchanged_file(self):
        excerpt = (
            "Исследование Результат\n"
            "B03.016.002 - Общий анализ крови. Врач: Т.Т.\n"
            "Гемоглобин 123 110.0 - 160.0\n"
        )
        answer = {"category": "lab", "title": "Анализы",
                  "evidence": "B03.016.002 - Общий анализ крови."}
        progress = []
        with patch("semantic_classifier.extract_excerpt", return_value=excerpt), \
             patch("semantic_classifier.request_classification", return_value=answer) as api:
            first = classify_documents_with_ai(
                [self.first], self.env, self.cache, progress=progress.append,
            )
            second = classify_documents_with_ai(
                [self.first], self.env, self.cache, progress=progress.append,
            )
        self.assertEqual(api.call_count, 1)
        self.assertEqual(first[self.first].category, "lab")
        self.assertEqual(second[self.first].category, "lab")
        self.assertTrue(any("локальный кэш" in line for line in progress))
        persisted = self.cache.read_text(encoding="utf-8")
        for private in ("Гемоглобин", "Общий анализ крови", "2026-11-18", "testing-only-dummy-value"):
            self.assertNotIn(private, persisted)
        payload = json.loads(persisted)
        self.assertEqual(payload["format"], 1)
        self.assertEqual(len(payload["results"]), 1)

    def test_no_text_no_api_routes_to_review(self):
        with patch("semantic_classifier.extract_excerpt", return_value=""), \
             patch("semantic_classifier.request_classification") as api:
            result = classify_documents_with_ai([self.first], self.env, self.cache)
        api.assert_not_called()
        self.assertEqual(result[self.first].category, "review")

    def test_network_error_aborts_before_any_output(self):
        make_pdf(self.first)
        with patch("pdf_merger.ROOT", self.root), patch(
            "semantic_classifier.extract_excerpt",
            return_value="B03.016.002 - Общий анализ крови. Врач: Т.Т.",
        ), patch(
            "semantic_classifier.request_classification",
            side_effect=ValueError("AI API ответил HTTP 429"),
        ), redirect_stdout(StringIO()):
            with self.assertRaisesRegex(ValueError, "HTTP 429"):
                process(
                    self.input, self.output, ai_classification="all",
                    env_file=self.env,
                )
        self.assertFalse(self.output.exists())
        self.assertTrue(self.first.exists())
        self.assertTrue(self.second.exists())

    def test_semantic_ai_all_routes_lab_and_exam_without_reusing_filename(self):
        excerpt_lab = "B03.016.002 - Общий анализ крови. Врач: Т.Т."
        excerpt_study = "7002892 - Рентгенография грудной клетки. Врач: Т.Т."
        def extract(path):
            return excerpt_lab if path.name == self.first.name else excerpt_study
        def ai(cfg, excerpt):
            if "Общий анализ" in excerpt:
                return {"category": "lab", "title": "Анализы",
                        "evidence": "B03.016.002 - Общий анализ крови."}
            return {"category": "study", "title": "Рентгенография грудной клетки",
                    "evidence": "7002892 - Рентгенография грудной клетки."}

        with patch("pdf_merger.ROOT", self.root), \
             patch("semantic_classifier.extract_excerpt", side_effect=extract), \
             patch("semantic_classifier.request_classification", side_effect=ai) as api, \
             redirect_stdout(StringIO()):
            result = process(self.input, self.output, ai_classification="all", env_file=self.env)
        self.assertEqual(result.groups_written, 2)
        self.assertEqual(api.call_count, 2)
        self.assertEqual(
            (self.output / "Анализы" / "2026.11.18-Анализы.pdf").read_bytes(),
            self.first.read_bytes(),
        )
        self.assertEqual(
            (self.output / "Обследования" / "2026.11.18-Рентгенография грудной клетки.pdf").read_bytes(),
            self.second.read_bytes(),
        )
        self.assertFalse((self.output / "На_проверку").exists())

    def test_review_only_sends_only_unclear_sources(self):
        with patch("pdf_merger.ROOT", self.root), patch("pdf_merger.classify_document",
                   side_effect=lambda path, effective_name=None:
                   DocumentClass("review", "Требует-проверки", "unknown")
                   if path.name == self.first.name else
                   DocumentClass("study", "ЭКГ", "known")), \
             patch("semantic_classifier.extract_excerpt",
                   return_value="B03.016.002 - Общий анализ крови. Врач: Т.Т."), \
             patch("semantic_classifier.request_classification",
                   return_value={"category": "lab", "title": "Анализы",
                                 "evidence": "B03.016.002 - Общий анализ крови."}) as api, \
             redirect_stdout(StringIO()):
            result = process(self.input, self.output,
                             ai_classification="review", env_file=self.env)
        self.assertEqual(api.call_count, 1)
        self.assertEqual(result.groups_written, 2)
        self.assertTrue((self.output / "Анализы" / "2026.11.18-Анализы.pdf").exists())


if __name__ == "__main__":
    unittest.main()

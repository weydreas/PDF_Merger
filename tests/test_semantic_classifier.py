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
        self.first = self.input / "2026-11-18-Требует-проверки.pdf"
        self.second = self.input / "2026-11-18-Требует-проверки-02.pdf"
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

    def test_unverified_evidence_and_generic_headers_do_not_merge(self):
        excerpt = (
            "Дата назначения лабораторного исследования\n"
            "7002892 - Рентгенография грудной клетки. Врач: Т.Т.\n"
        )
        candidates = (
            {"category": "lab", "title": "Анализы",
             "evidence": "Дата назначения лабораторного исследования"},
            {"category": "lab", "title": "Анализы",
             "evidence": "Общий анализ крови"},
            {"category": "study", "title": "КТ уважаемого пациента",
             "evidence": "7002892 - Рентгенография грудной клетки."},
            {"category": "lab", "title": "Анализы",
             "evidence": "7002892 - Рентгенография грудной клетки."},
        )
        for candidate in candidates:
            with self.subTest(candidate=candidate):
                result = _sanitize_classification(
                    candidate, excerpt,
                    DocumentClass("study", "Рентгенография грудной клетки", "text"),
                )
                self.assertEqual(result.category, "review")

    def test_structural_mixed_procedures_are_not_trusted_from_ai(self):
        excerpt = (
            "7002892 - Рентгенография грудной клетки. Врач: Т.Т.\n"
            "B03.016.002 - Общий анализ крови. Врач: Т.Т.\n"
        )
        answer = {
            "category": "lab", "title": "Анализы",
            "evidence": "B03.016.002 - Общий анализ крови.",
        }
        local = DocumentClass("review", "Требует-проверки",
                              "неоднозначный набор проведённых процедур")
        self.assertEqual(
            _sanitize_classification(answer, excerpt, local).category, "review",
        )

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
        with patch(
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
            return excerpt_lab if path == self.first else excerpt_study
        def ai(cfg, excerpt):
            if "Общий анализ" in excerpt:
                return {"category": "lab", "title": "Анализы",
                        "evidence": "B03.016.002 - Общий анализ крови."}
            return {"category": "study", "title": "Рентгенография грудной клетки",
                    "evidence": "7002892 - Рентгенография грудной клетки."}

        with patch("semantic_classifier.extract_excerpt", side_effect=extract), \
             patch("semantic_classifier.request_classification", side_effect=ai) as api, \
             redirect_stdout(StringIO()):
            result = process(self.input, self.output, ai_classification="all", env_file=self.env)
        self.assertEqual(result.groups_written, 2)
        self.assertEqual(api.call_count, 2)
        self.assertEqual(
            (self.output / "Анализы" / "2026-11-18-Анализы.pdf").read_bytes(),
            self.first.read_bytes(),
        )
        self.assertEqual(
            (self.output / "Обследования" / "2026-11-18-Рентгенография грудной клетки.pdf").read_bytes(),
            self.second.read_bytes(),
        )
        self.assertFalse((self.output / "На_проверку").exists())

    def test_review_only_sends_only_unclear_sources(self):
        with patch("pdf_merger.classify_document",
                   side_effect=lambda path, effective_name=None:
                   DocumentClass("review", "Требует-проверки", "unknown")
                   if path == self.first else
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
        self.assertTrue((self.output / "Анализы" / "2026-11-18-Анализы.pdf").exists())


if __name__ == "__main__":
    unittest.main()

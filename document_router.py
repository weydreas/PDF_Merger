"""Conservative, local-only classification and safe descriptive labels for dated PDFs.

A document is merged with daily lab results ONLY on positive lab evidence.
Imaging and other examinations always remain individual PDFs. Ambiguous
documents are kept separate for manual review. No network or AI calls.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

from pypdf import PdfReader


@dataclass(frozen=True)
class DocumentClass:
    category: str  # "lab", "study", or "review"
    title: str
    reason: str


# Specific modality names must not be conflated with "tests"/"исследования".
# Test the presence of explicit modality terms in the actual extracted text.
STUDY_TERMS: tuple[tuple[str, str], ...] = (
    ("Рентгенография", r"рентгенограф\w*|рентгеновск\w*\s+сним\w*|\bx[- ]?ray\b"),
    ("Компьютерная томография", r"компьютерн\w*\s+томограф\w*|\bкт\s+(?:органов|головы|мозга|грудной|брюшной|позвоночника)\b|\bct\s+scan\b"),
    ("МРТ", r"магнитно[ -]?резонансн\w*\s+томограф\w*|\bмрт\b|\bmri\b"),
    ("УЗИ", r"ультразвуков\w*\s+исследован\w*|ультрасонограф\w*|\bузи\b|\bultrasound\b"),
    ("ЭКГ", r"электрокардиограф\w*|\bэкг\b|\becg\b|\bekg\b"),
    ("ЭхоКГ", r"эхокардиограф\w*|\bэхокг\b|\bechocardiograph\w*"),
    ("ЭЭГ", r"электроэнцефалограф\w*|\bээг\b|\beeg\b"),
    ("Эндоскопия", r"эндоскоп\w*|гастроскоп\w*|колоноскоп\w*|фгдс\b"),
    ("Маммография", r"маммограф\w*|\bmammograph\w*"),
    ("Денситометрия", r"денситометр\w*|\bdexa\b"),
    ("Спирометрия", r"спирометр\w*|\bspirometr\w*"),
    ("Холтер", r"холтер\w*|holter\b"),
)
STUDY_REGEXES = tuple((label, re.compile(pattern, re.IGNORECASE)) for label, pattern in STUDY_TERMS)

LAB_TERMS: tuple[tuple[str, str], ...] = (
    ("Общий анализ крови", r"общ\w*\s+анализ\w*\s+кров\w*|клиническ\w*\s+анализ\w*\s+кров\w*|\bоак\b|\bcbc\b"),
    ("Биохимия крови", r"биохимическ\w*\s+анализ\w*|\bбиохимия\b|blood\s+chemistry\b"),
    ("Анализ мочи", r"общ\w*\s+анализ\w*\s+моч\w*|\bоам\b|urinalysis\b"),
    ("Гормоны", r"тиреотропн\w*|\bттг\b|гормональн\w*\s+панел\w*|анализ\w*\s+на\s+гормон\w*"),
    ("Лабораторные анализы", r"лабораторн\w*\s+(?:анализ\w*|исследован\w*|результат\w*)|\blab(?:oratory)?\s+(?:results?|tests?|report)\b"),
)
LAB_REGEXES = tuple((label, re.compile(pattern, re.IGNORECASE)) for label, pattern in LAB_TERMS)

REGIONS: tuple[tuple[str, str], ...] = (
    ("грудной клетки", r"грудн\w*\s+клетк\w*|орган\w*\s+грудн\w*\s+клетк\w*"),
    ("позвоночника", r"позвоночник\w*"),
    ("коленного сустава", r"коленн\w*\s+сустав\w*"),
    ("головного мозга", r"головн\w*\s+мозг\w*"),
    ("брюшной полости", r"брюшн\w*\s+полост\w*"),
    ("щитовидной железы", r"щитовидн\w*\s+желез\w*"),
)
REGION_REGEXES = tuple((label, re.compile(pattern, re.IGNORECASE)) for label, pattern in REGIONS)


def classify_text(filename: str, excerpt: str) -> DocumentClass:
    """Use explicit document text first; filename alone is a secondary hint.

    Broad words "test", "анализ", "исследование" do not prove the category.
    If multiple modalities are present, do not guess which one is the report.
    """
    name = re.sub(r"^\d{4}-\d{2}-\d{2}-", "", Path(filename).stem).replace("_", " ")
    header = excerpt[:2500]
    study_hits = [(label, pattern.search(header)) for label, pattern in STUDY_REGEXES]
    text_studies = [(label, match) for label, match in study_hits if match]
    name_studies = [label for label, pattern in STUDY_REGEXES if pattern.search(name)]
    text_lab = [label for label, pattern in LAB_REGEXES if pattern.search(header)]
    name_lab = [label for label, pattern in LAB_REGEXES if pattern.search(name)]

    # Text carries more weight than an AI-renamed filename: an X-ray labelled
    # "Tests" must stay an X-ray. Different procedures in one report are unsafe.
    if text_studies:
        unique = sorted({label for label, _ in text_studies})
        if len(unique) != 1:
            return DocumentClass("review", "Требует-проверки", "указано несколько видов обследований")
        study = unique[0]
        # A lab title at the start combined with a modality is ambiguous.
        if text_lab and text_studies[0][1].start() > 650:
            return DocumentClass("review", "Требует-проверки", "конфликт вида исследования и лабораторного отчёта")
        title = study
        if study in {"Рентгенография", "Компьютерная томография", "МРТ", "УЗИ"}:
            # Only take an anatomical region near the confirmed study term.
            found = text_studies[0][1]
            nearby = header[max(0, found.start() - 30):found.end() + 110]
            regions = [region for region, regex in REGION_REGEXES if regex.search(nearby)]
            if len(regions) == 1:
                title = f"{study} {regions[0]}"
        return DocumentClass("study", title, "тип обследования подтверждён текстом")

    if text_lab:
        # Prefer the most specific laboratory title over a generic heading.
        specific = [label for label in text_lab if label != "Лабораторные анализы"]
        title = specific[0] if len(set(specific)) == 1 else "Анализы"
        return DocumentClass("lab", title, "лабораторный формат подтверждён текстом")

    # No extractable text: only very specific names justify classification.
    if len(set(name_studies)) == 1 and not name_lab:
        return DocumentClass("study", name_studies[0], "явный тип обследования в имени")
    if len(set(name_lab)) == 1 and not name_studies:
        return DocumentClass("lab", name_lab[0], "явный тип анализа в имени")
    return DocumentClass("review", "Требует-проверки", "недостаточно достоверных признаков")


def classify_document(path: Path, effective_name: str | None = None) -> DocumentClass:
    """Local classification; unreadable PDFs cannot be silently added to labs."""
    try:
        reader = PdfReader(str(path), strict=False)
        if reader.is_encrypted:
            return DocumentClass("review", "Требует-проверки", "PDF защищён паролем")
        text = "\n".join((page.extract_text() or "") for page in reader.pages[:2])[:6000]
    except Exception:
        return DocumentClass("review", "Требует-проверки", "текст PDF не извлечён")
    return classify_text(effective_name or path.name, text)

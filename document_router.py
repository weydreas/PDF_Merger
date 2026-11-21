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
    ("КТ", r"компьютерн\w*\s+томограф\w*|\bкт\s+(?:органов|головы|мозга|грудной|брюшной|позвоночника)\b|\bct\s+scan\b"),
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


# Hospital LIS exports use the generic field "Дата назначения лабораторного
# исследования" even for ECG and X-ray. The performed procedure is the
# CODE - PROCEDURE. Врач: ... row; use THAT rather than incidental text.
PROCEDURE_ROW = re.compile(
    r"(?m)^[ \t]*([A-Za-zА-Яа-я]?\d{3,}(?:\.\d+){0,4})[ \t]*-[ \t]*"
    r"(.+?)(?:[ \t]+Врач\s*:|$)",
    re.IGNORECASE,
)
# Specific, known lab test code families; not every A-prefixed code is a lab.
LAB_CODE = re.compile(r"^(?:A09(?:\.|$)|A12\.(?:05|30)(?:\.|$)|A26(?:\.|$)|B03(?:\.|$))", re.I)


def _study_title(kind: str, procedure: str) -> str:
    if kind not in {"Рентгенография", "КТ", "МРТ", "УЗИ"}:
        return kind
    regions = [title for title, rx in REGION_REGEXES if rx.search(procedure)]
    return f"{kind} {regions[0]}" if len(regions) == 1 else kind


def _lab_title(labels: list[str]) -> str:
    specific = [lab for lab, pattern in LAB_REGEXES
                if lab != "Лабораторные анализы"
                and any(pattern.search(label) for label in labels)]
    return specific[0] if len(set(specific)) == 1 else "Анализы"



def classify_text(filename: str, excerpt: str) -> DocumentClass:
    """Classify by the performed procedure, not export headers or referrals.

    The filename (including any previous AI title) is never proof of a lab.
    No text or conflicting procedure rows => individual manual review.
    """
    header = excerpt[:6000]
    rows = [(match.group(1), match.group(2).strip(" \t."))
            for match in PROCEDURE_ROW.finditer(header)]
    if rows:
        studies: list[tuple[str, str]] = []
        labs: list[str] = []
        unknown: list[str] = []
        for code, procedure in rows:
            kinds = [kind for kind, pattern in STUDY_REGEXES
                     if pattern.search(procedure)]
            if len(kinds) == 1:
                studies.append((kinds[0], procedure))
            elif len(kinds) > 1:
                return DocumentClass("review", "Требует-проверки",
                                     "в одном названии процедуры несколько модальностей")
            elif LAB_CODE.match(code):
                labs.append(procedure)
            else:
                unknown.append(procedure)

        if len(studies) == 1 and not labs and not unknown:
            kind, procedure = studies[0]
            return DocumentClass("study", _study_title(kind, procedure),
                                 "вид обследования подтверждён строкой проведённой процедуры")
        if not studies and labs and not unknown:
            return DocumentClass("lab", _lab_title(labs),
                                 "лабораторный тип подтверждён кодами проведённых анализов")
        # Multiple actual studies, unknown procedures, mixed labs/studies:
        # do not guess or combine them, even when a generic lab header exists.
        return DocumentClass("review", "Требует-проверки",
                             "неоднозначный набор проведённых процедур")

    # Other providers have free-text reports without structured procedure rows.
    # A *specific* type can be recovered when there is no competing evidence;
    # generic "лабораторное исследование" boilerplate does not prove "lab".
    search_text = header[:2500]
    study_kinds = [kind for kind, pattern in STUDY_REGEXES
                   if pattern.search(search_text)]
    specific_labs = [kind for kind, pattern in LAB_REGEXES
                     if kind != "Лабораторные анализы" and pattern.search(search_text)]
    if len(study_kinds) == 1 and not specific_labs:
        kind = study_kinds[0]
        return DocumentClass("study", _study_title(kind, search_text),
                             "вид обследования подтверждён текстом")
    if not study_kinds and specific_labs:
        return DocumentClass("lab", _lab_title([search_text]),
                             "подтверждён конкретный лабораторный анализ")
    return DocumentClass("review", "Требует-проверки",
                         "нет однозначного названия проведённой процедуры")


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

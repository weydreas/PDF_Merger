"""Offline check that every simple requirement is installed in the local venv.

The packaging parser is bundled with pip, so this check needs no extra SDK.
Exit codes: 0 = satisfied, 1 = packages missing/outdated, 2 = invalid setup.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
import sys

from pip._vendor.packaging.requirements import InvalidRequirement, Requirement


def check_requirements(requirements_file: Path) -> int:
    try:
        lines = requirements_file.read_text(encoding="utf-8-sig").splitlines()
    except OSError as exc:
        print(f"Не удалось прочитать requirements.txt: {exc}", file=sys.stderr)
        return 2

    unsatisfied: list[str] = []
    for line_number, line in enumerate(lines, start=1):
        spec = line.partition("#")[0].strip()
        if not spec:
            continue
        try:
            requirement = Requirement(spec)
        except InvalidRequirement:
            print(
                f"Неподдерживаемое требование в строке {line_number}: {spec}",
                file=sys.stderr,
            )
            return 2
        if requirement.url:
            print(
                f"Прямая ссылка в строке {line_number} требует отдельной проверки безопасности.",
                file=sys.stderr,
            )
            return 2
        if requirement.marker and not requirement.marker.evaluate():
            continue
        try:
            installed_version = version(requirement.name)
        except PackageNotFoundError:
            unsatisfied.append(spec)
            continue
        if not requirement.specifier.contains(installed_version, prereleases=True):
            unsatisfied.append(f"{spec} (установлена версия {installed_version})")

    if unsatisfied:
        print("Требуется установка или обновление библиотек:")
        for item in unsatisfied:
            print(f"  - {item}")
        return 1

    print("Зависимости установлены, версии соответствуют requirements.txt.")
    return 0


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    if len(arguments) != 1:
        print("Использование: python dependency_check.py requirements.txt", file=sys.stderr)
        return 2
    return check_requirements(Path(arguments[0]))


if __name__ == "__main__":
    raise SystemExit(main())

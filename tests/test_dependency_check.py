"""Dependency checks never contact the internet or use real document data."""
from contextlib import redirect_stderr, redirect_stdout
from importlib.metadata import PackageNotFoundError
from io import StringIO
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from dependency_check import check_requirements, main


class DependencyCheckTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.req = Path(self.temporary.name) / "requirements.txt"

    def check(self, spec: str, installed_version: str = "6.0") -> tuple[int, str]:
        self.req.write_text(spec, encoding="utf-8")
        output = StringIO()
        with patch("dependency_check.version", return_value=installed_version), redirect_stdout(output):
            code = check_requirements(self.req)
        return code, output.getvalue()

    def test_satisfied_range_needs_no_install(self):
        status, output = self.check("pypdf>=5.9,<8\n")
        self.assertEqual(status, 0)
        self.assertIn("Зависимости установлены", output)

    def test_too_old_or_too_new_recommends_update(self):
        for installed in ("5.8", "8.0"):
            with self.subTest(installed=installed):
                status, output = self.check("pypdf>=5.9,<8\n", installed)
                self.assertEqual(status, 1)
                self.assertIn("pypdf", output)

    def test_missing_dependency_triggers_install(self):
        self.req.write_text("pypdf>=5.9,<8\n", encoding="utf-8")
        with patch("dependency_check.version", side_effect=PackageNotFoundError("pypdf")), redirect_stdout(StringIO()) as output:
            self.assertEqual(check_requirements(self.req), 1)
        self.assertIn("pypdf", output.getvalue())

    def test_checks_all_requirements_and_version_constraints(self):
        self.req.write_text("pypdf>=5.9,<8\nother-package==2.0\n", encoding="utf-8")
        def fake_version(name):
            return {"pypdf": "6.0", "other-package": "1.9"}[name]
        with patch("dependency_check.version", side_effect=fake_version), redirect_stdout(StringIO()) as output:
            self.assertEqual(check_requirements(self.req), 1)
        self.assertIn("other-package", output.getvalue())

    def test_unexpected_requirement_option_fails_closed(self):
        self.req.write_text("-r another-requirements.txt\n", encoding="utf-8")
        with redirect_stderr(StringIO()) as error:
            self.assertEqual(check_requirements(self.req), 2)
        self.assertIn("Неподдерживаемое", error.getvalue())

    def test_missing_requirements_fails_closed(self):
        with redirect_stderr(StringIO()):
            self.assertEqual(main([str(self.req)]), 2)

    def test_pep508_marker_skips_unneeded_dependency(self):
        status, _ = self.check("fake-package>99; python_version < '2.0'\n")
        self.assertEqual(status, 0)


if __name__ == "__main__":
    unittest.main()

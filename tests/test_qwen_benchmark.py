"""Static contract checks for optional local Ollama benchmark.

No model pulls, GPU allocations, HTTP calls, or medical PDFs in CI.
"""
from pathlib import Path
import shutil
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parent.parent


class QwenBenchmarkTests(unittest.TestCase):
    def setUp(self):
        self.cmd = (ROOT / "test-qwen.cmd").read_text(encoding="utf-8").lower()
        self.ps_path = ROOT / "scripts" / "test-qwen-vl.ps1"
        self.raw = self.ps_path.read_bytes()
        self.ps = self.ps_path.read_text(encoding="utf-8-sig").lower()

    def test_one_click_launcher_runs_local_script_with_explicit_install(self):
        self.assertIn("%~dp0scripts\\test-qwen-vl.ps1", self.cmd)
        self.assertIn("powershell.exe -nologo -noprofile", self.cmd)
        self.assertIn("-install", self.cmd)
        self.assertIn("pause >nul", self.cmd)

    def test_script_requires_local_ollama_and_does_not_touch_patient_data(self):
        self.assertTrue(self.raw.startswith(b"\xef\xbb\xbf"))
        self.assertIn("param([switch]$install", self.ps)
        self.assertIn("http://127.0.0.1:11434", self.ps)
        self.assertIn("/api/tags", self.ps)
        self.assertIn("/api/generate", self.ps)
        self.assertIn("ollama pull $modelname", self.ps)
        self.assertIn("if (-not $install)", self.ps)
        self.assertIn("9gb", self.ps)
        self.assertIn("qwen3-vl:8b", self.ps)
        for forbidden in (
            "api.openai.com", "curl |", "iex", "reset --hard",
            "ollama rm", "remove-item", "get-childitem -path input",
            "get-content .env", "pdfmerger_ai_key",
        ):
            self.assertNotIn(forbidden, self.ps)

    def test_synthetic_cases_cover_semantic_and_vision(self):
        for word in ("110.0 - 160.0", "рентгенография", "рекомендовано узи",
                     "ecg report", "вымышленные пдн", "baselinemodel"):
            self.assertIn(word, self.ps)
        self.assertIn("system.drawing.bitmap", self.ps)
        self.assertIn("только вымышленные", self.ps)

    @unittest.skipUnless(sys.platform == "win32" and shutil.which("powershell.exe"),
                         "Windows PowerShell parser only")
    def test_powershell_parser_no_execution(self):
        path = str(self.ps_path).replace("'", "''")
        command = (
            "$errors=$null; "
            "[System.Management.Automation.Language.Parser]::ParseFile("
            "'" + path + "',[ref]$null,[ref]$errors) | Out-Null; "
            "if ($errors.Count -gt 0) { "
            "$errors | ForEach-Object { Write-Host $_.Message }; exit 1 }"
        )
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
            capture_output=True, text=True, timeout=30,
            encoding="utf-8", errors="replace",
        )
        self.assertEqual(result.returncode, 0, result.stdout + "\n" + result.stderr)


if __name__ == "__main__":
    unittest.main()

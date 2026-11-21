"""Safety checks for CPU-only Surya OCR pilot (no network/model download in CI)."""

from pathlib import Path
import shutil
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parent.parent


class SuryaCPUPilotTests(unittest.TestCase):
    def setUp(self):
        self.cmd = (ROOT / "test-surya-cpu.cmd").read_text(encoding="utf-8").lower()
        self.script_path = ROOT / "scripts" / "test-surya-cpu.ps1"
        self.raw = self.script_path.read_bytes()
        self.script = self.script_path.read_text(encoding="utf-8-sig").lower()
        self.ignore = (ROOT / ".gitignore").read_text(encoding="utf-8").lower()

    def test_one_click_launcher_and_private_isolation(self):
        self.assertIn("%~dp0scripts\\test-surya-cpu.ps1", self.cmd)
        self.assertIn("powershell.exe -nologo -noprofile", self.cmd)
        self.assertIn("-install", self.cmd)
        self.assertIn("pause >nul", self.cmd)
        self.assertIn("/.venv-surya-cpu/", self.ignore)
        self.assertIn("/.local-tools/", self.ignore)
        self.assertIn("/logs/", self.ignore)

    def test_surya_is_cpu_only_and_pinned(self):
        self.assertTrue(self.raw.startswith(b"\xef\xbb\xbf"))
        for required in (
            "surya-ocr==0.24.0",
            "surya-2.5-q8_0.gguf", "surya-2.5-mmproj.gguf",
            "datalab-to/surya-ocr-2.5-gguf",
            "$env:surya_inference_backend = 'llamacpp'",
            "$env:llama_cpp_ngl = '0'",
            "$env:torch_device = 'cpu'",
            "$env:surya_inference_parallel = '1'",
            "surya-cpu-pilot", "synthetic-medical-page.png",
            "llama-server.exe", "get-filehash", "sha256", "425eeb2a94a9600",
        ):
            self.assertIn(required, self.script)
        for forbidden in (
            "api.openai.com", "ollama pull", "llama-server -hf",
            "git reset", "git clean", "ollama rm", "input\\*.pdf",
            "output\\*.pdf", "pdf_merger.py --overwrite", "pip install --user",
        ):
            self.assertNotIn(forbidden, self.script)

    def test_install_is_explicit_and_binary_digest_verified_first(self):
        self.assertIn("param([switch]$install)", self.script)
        self.assertIn("if (-not $install)", self.script)
        self.assertLess(self.script.index("$actualsha"), self.script.index("expand-archive"))
        self.assertIn("$actualsha -ne $archivesha", self.script)
        self.assertIn("get-ciminstance win32_computersystem", self.script)

    @unittest.skipUnless(sys.platform == "win32" and shutil.which("powershell.exe"),
                         "PowerShell 5.1 parser runs on Windows only")
    def test_powershell_parser_no_execution(self):
        path = str(self.script_path).replace("'", "''")
        command = (
            "$errors=$null; "
            "[System.Management.Automation.Language.Parser]::ParseFile("
            "'" + path + "',[ref]$null,[ref]$errors) | Out-Null; "
            "if ($errors.Count -gt 0) { "
            "$errors | ForEach-Object { Write-Host $_.Message }; exit 1 }"
        )
        result = subprocess.run(
            ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", command],
            capture_output=True, text=True, timeout=30, encoding="utf-8", errors="replace",
        )
        self.assertEqual(result.returncode, 0, result.stdout + "\n" + result.stderr)


if __name__ == "__main__":
    unittest.main()

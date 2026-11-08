"""Windows launcher contract checks; real console flow runs on Windows CI only."""

from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from pypdf import PdfReader, PdfWriter

ROOT = Path(__file__).resolve().parent.parent


class LauncherContractTests(unittest.TestCase):
    def test_powershell_is_utf8_bom_for_windows_powershell_51(self):
        self.assertTrue((ROOT / 'merge_windows.ps1').read_bytes().startswith(b'\xef\xbb\xbf'))

    def test_cmd_always_pauses_even_on_error(self):
        source = (ROOT / 'start.cmd').read_text(encoding='utf-8').lower()
        self.assertIn('goto :finish', source)
        self.assertIn(':finish', source)
        self.assertIn('pause >nul', source)
        self.assertIn('powershell.exe', source)

    def test_old_launcher_routes_to_new_entrypoint(self):
        source = (ROOT / 'merge_windows.bat').read_text(encoding='utf-8').lower()
        self.assertIn('start.cmd', source)


@unittest.skipUnless(sys.platform == 'win32', 'Requires Windows PowerShell 5.1')
class WindowsConsoleFlowTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        for filename in ('merge_windows.ps1', 'pdf_merger.py', 'requirements.txt'):
            shutil.copy2(ROOT / filename, self.root / filename)

    def invoke_ps(self, answer=''):
        return subprocess.run(
            ["powershell.exe", "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-File", str(self.root / 'merge_windows.ps1')],
            input=answer, capture_output=True, text=True, encoding='utf-8', errors='replace',
            timeout=180,
        )

    def test_first_run_creates_folders_and_stops(self):
        first = self.invoke_ps()
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        self.assertTrue((self.root / 'input').is_dir())
        self.assertTrue((self.root / 'output').is_dir())
        self.assertFalse((self.root / '.venv').exists())
        self.assertIn('[CREATED]', first.stdout)
        second = self.invoke_ps()
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        self.assertFalse((self.root / '.venv').exists())
        self.assertIn('[READY]', second.stdout)

    def test_yes_no_flow_and_pages(self):
        self.assertEqual(self.invoke_ps().returncode, 0)
        for filename, width in [('2026-03-02-02-Beta.pdf', 222),
                                ('2026-03-02-01-Alpha.pdf', 111)]:
            writer = PdfWriter()
            writer.add_blank_page(width=width, height=100)
            with (self.root / 'input' / filename).open('wb') as handle:
                writer.write(handle)
            writer.close()

        canceled = self.invoke_ps('0\n')
        self.assertEqual(canceled.returncode, 0, canceled.stdout + canceled.stderr)
        self.assertIn('[1]', canceled.stdout)
        self.assertFalse((self.root / 'output' / '2026-03-02.pdf').exists())
        self.assertFalse((self.root / '.venv').exists())

        approved = self.invoke_ps('1\n')
        self.assertEqual(approved.returncode, 0, approved.stdout + approved.stderr)
        merged = self.root / 'output' / '2026-03-02.pdf'
        self.assertTrue(merged.exists())
        self.assertEqual([int(page.mediabox.width) for page in PdfReader(merged).pages], [111, 222])
        again = self.invoke_ps('1\n')
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
        self.assertEqual([int(page.mediabox.width) for page in PdfReader(merged).pages], [111, 222])


if __name__ == '__main__':
    unittest.main()

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
        for filename in ('merge_windows.ps1', 'pdf_merger.py', 'ai_renamer.py', 'dependency_check.py', 'requirements.txt'):
            shutil.copy2(ROOT / filename, self.root / filename)

    def invoke_ps(self, answer=''):
        return subprocess.run(
            ["powershell.exe", "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-File", str(self.root / 'merge_windows.ps1')],
            input=answer, capture_output=True, text=True, encoding='utf-8', errors='replace',
            timeout=180,
        )

    def test_first_run_prompts_installs_and_subsequent_run_skips_install(self):
        canceled = self.invoke_ps('0\n')
        self.assertEqual(canceled.returncode, 0, canceled.stdout + canceled.stderr)
        self.assertTrue((self.root / 'input').is_dir())
        self.assertTrue((self.root / 'output').is_dir())
        self.assertFalse((self.root / '.venv').exists())
        self.assertIn('[CREATED]', canceled.stdout)
        self.assertIn('Необходимо установить дополнительные библиотеки', canceled.stdout)

        approved = self.invoke_ps('\n')
        self.assertEqual(approved.returncode, 0, approved.stdout + approved.stderr)
        self.assertTrue((self.root / '.venv' / 'Scripts' / 'python.exe').exists())
        self.assertIn('Установка завершена', approved.stdout)
        self.assertIn('[READY]', approved.stdout)

        repeated = self.invoke_ps()
        self.assertEqual(repeated.returncode, 0, repeated.stdout + repeated.stderr)
        self.assertNotIn('Нажмите Enter для установки', repeated.stdout)
        self.assertNotIn('Устанавливаются библиотеки', repeated.stdout)
        self.assertIn('Зависимости установлены', repeated.stdout)

    def test_yes_no_flow_and_pages(self):
        self.assertEqual(self.invoke_ps('\n').returncode, 0)
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
        self.assertTrue((self.root / '.venv').exists())

        approved = self.invoke_ps('1\n')
        self.assertEqual(approved.returncode, 0, approved.stdout + approved.stderr)
        merged = self.root / 'output' / '2026-03-02.pdf'
        self.assertTrue(merged.exists())
        self.assertEqual([int(page.mediabox.width) for page in PdfReader(merged).pages], [111, 222])
        again = self.invoke_ps('1\n')
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
        self.assertEqual([int(page.mediabox.width) for page in PdfReader(merged).pages], [111, 222])


    def test_changed_requirements_trigger_install_prompt_before_processing(self):
        self.assertEqual(self.invoke_ps('\n').returncode, 0)
        (self.root / 'requirements.txt').write_text('pypdf>=999\n', encoding='utf-8')
        blocked = self.invoke_ps('0\n')
        self.assertEqual(blocked.returncode, 0, blocked.stdout + blocked.stderr)
        self.assertIn('Требуется установка или обновление библиотек', blocked.stdout)
        self.assertIn('Нажмите Enter для установки', blocked.stdout)
        self.assertIn('Установка отменена', blocked.stdout)
        self.assertFalse((self.root / 'output' / '2026-03-02.pdf').exists())

    def test_nonstandard_name_requires_explicit_ai_consent(self):
        self.assertEqual(self.invoke_ps('\n').returncode, 0)
        writer = PdfWriter()
        writer.add_blank_page(width=100, height=100)
        with (self.root / 'input' / 'scan.pdf').open('wb') as handle:
            writer.write(handle)
        writer.close()

        canceled = self.invoke_ps('1\n0\n')
        self.assertEqual(canceled.returncode, 0, canceled.stdout + canceled.stderr)
        self.assertIn('[2]', canceled.stdout)
        self.assertFalse((self.root / 'output' / 'scan.pdf').exists())

        offline = self.invoke_ps('1\n2\n')
        self.assertEqual(offline.returncode, 0, offline.stdout + offline.stderr)
        self.assertTrue((self.root / 'input' / 'scan.pdf').exists())
        self.assertFalse(any((self.root / 'output').glob('*.pdf')))

        # Missing key is reported; no request is made or PDF renamed.
        ai = self.invoke_ps('1\n1\n')
        self.assertNotEqual(ai.returncode, 0)
        self.assertIn('PDF_MERGER_API_KEY', ai.stdout + ai.stderr)
        self.assertTrue((self.root / 'input' / 'scan.pdf').exists())


if __name__ == '__main__':
    unittest.main()

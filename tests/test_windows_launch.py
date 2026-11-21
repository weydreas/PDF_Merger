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

    def test_python_output_is_unbuffered_for_ai_progress(self):
        source = (ROOT / 'merge_windows.ps1').read_text(encoding='utf-8-sig')
        self.assertIn("PYTHONUNBUFFERED = '1'", source)
        self.assertIn('& $python -u $MergeProgram @mergeArgs', source)
        self.assertIn("logs\\ai-progress.log", source)

    def test_old_launcher_routes_to_new_entrypoint(self):
        source = (ROOT / 'merge_windows.bat').read_text(encoding='utf-8').lower()
        self.assertIn('start.cmd', source)


@unittest.skipUnless(sys.platform == 'win32', 'Requires Windows PowerShell 5.1')
class WindowsConsoleFlowTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        for filename in ('merge_windows.ps1', 'pdf_merger.py', 'ai_renamer.py', 'document_router.py', 'dependency_check.py', 'requirements.txt', '.env.example'):
            shutil.copy2(ROOT / filename, self.root / filename)

    def invoke_ps(self, answer=''):
        # PowerShell 5.1 native subprocesses may consume piped stdin before
        # Read-Host runs. Stub *only the host input function* in this test process.
        # Production start.cmd still uses the real interactive Read-Host.
        answers = answer.splitlines()
        literals = ", ".join("'" + value.replace("'", "''") + "'" for value in answers)
        script_path = str(self.root / 'merge_windows.ps1').replace("'", "''")
        command = (
            "$global:__testAnswers = @(" + literals + "); "
            "$global:__testIndex = 0; "
            "function global:Read-Host { param([string]$Prompt) "
            "if ($global:__testIndex -ge $global:__testAnswers.Count) { return '0' }; "
            "$answer = $global:__testAnswers[$global:__testIndex]; "
            "$global:__testIndex++; return [string]$answer }; "
            "& '" + script_path + "'"
        )
        return subprocess.run(
            ["powershell.exe", "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-Command", command],
            input='', capture_output=True, text=True, encoding='utf-8', errors='replace',
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
        self.assertTrue((self.root / '.env').is_file())
        self.assertEqual(
            (self.root / '.env').read_bytes(),
            (self.root / '.env.example').read_bytes(),
        )
        self.assertIn('insert your key here', (self.root / '.env').read_text(encoding='utf-8'))

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

    def test_env_with_user_key_is_not_overwritten(self):
        existing = self.root / '.env'
        existing.write_text(
            'PDF_MERGER_API_KEY=local-test-value\\n'.replace('\\n', '\n'),
            encoding='utf-8',
        )
        first = self.invoke_ps('0\n')
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        self.assertEqual(existing.read_text(encoding='utf-8'), 'PDF_MERGER_API_KEY=local-test-value\n')
        second = self.invoke_ps('0\n')
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        self.assertEqual(existing.read_text(encoding='utf-8'), 'PDF_MERGER_API_KEY=local-test-value\n')
        self.assertNotIn('[CREATED] .env', first.stdout + second.stdout)

    def test_missing_template_does_not_block_offline_setup(self):
        (self.root / '.env.example').unlink()
        first = self.invoke_ps('0\n')
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        self.assertIn('шаблон .env.example отсутствует', first.stdout)
        self.assertFalse((self.root / '.env').exists())

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
        # Synthetic blank PDFs have no lab evidence: never mix them into a
        # daily bundle on the strength of their filename alone.
        folder = self.root / 'output' / 'На_проверку'
        first = folder / '2026-03-02-Требует-проверки.pdf'
        second = folder / '2026-03-02-Требует-проверки-02.pdf'
        self.assertTrue(first.exists())
        self.assertTrue(second.exists())
        self.assertEqual([int(page.mediabox.width) for page in PdfReader(first).pages], [111])
        self.assertEqual([int(page.mediabox.width) for page in PdfReader(second).pages], [222])
        self.assertFalse((self.root / 'output' / '2026-03-02.pdf').exists())
        again = self.invoke_ps('1\n')
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
        self.assertEqual([int(page.mediabox.width) for page in PdfReader(first).pages], [111])
        self.assertEqual([int(page.mediabox.width) for page in PdfReader(second).pages], [222])


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
        self.assertIn('AI: запускается проверка документов', ai.stdout)
        self.assertIn('Начало: 1 PDF', ai.stdout)
        self.assertTrue((self.root / 'input' / 'scan.pdf').exists())


if __name__ == '__main__':
    unittest.main()

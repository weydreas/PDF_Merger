"""AI preflight regression tests. Fake suggestions and synthetic PDFs only."""
from contextlib import redirect_stderr, redirect_stdout
from io import BytesIO, StringIO
import json
from pathlib import Path
import os
import tempfile
import unittest
from unittest.mock import patch

from pypdf import PdfReader, PdfWriter

from ai_renamer import AIConfig, AIProgressLog, RenameError, _verified_date, load_config, plan_renames, request_suggestion
from pdf_merger import main, process


def make_pdf(path: Path, width: int = 100) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    writer = PdfWriter()
    writer.add_blank_page(width=width, height=100)
    with path.open('wb') as handle:
        writer.write(handle)
    writer.close()


class AIRenameTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.input = self.root / 'input'
        self.output = self.root / 'output'
        self.input.mkdir()
        self.env = self.root / '.env'
        self.env.write_text('PDF_MERGER_API_KEY=test-secret\nPDF_MERGER_AI_MODEL=test-model\n', encoding='utf-8')
        self.clean_env = patch.dict(os.environ, {'PDF_MERGER_API_KEY': '', 'PDF_MERGER_API_URL': '', 'PDF_MERGER_AI_MODEL': ''})
        self.clean_env.start()
        self.addCleanup(self.clean_env.stop)

    def suggestion(self, day='2026-05-19', evidence='19.05.2026', title='Биохимия'):
        return {'date': day, 'evidence': evidence, 'title': title}

    def test_renames_and_merges_with_ai(self):
        src = self.input / 'scan.pdf'
        make_pdf(src, 173)
        original = src.read_bytes()
        with patch('ai_renamer.extract_excerpt', return_value='Document dated 19.05.2026'), \
             patch('ai_renamer.request_suggestion', return_value=self.suggestion()) as api, \
             redirect_stdout(StringIO()):
            result = process(self.input, self.output, rename_with_ai=True, env_file=self.env)
        self.assertEqual(result.files_dated, 1)
        self.assertEqual(result.groups_written, 1)
        new = self.input / '2026-05-19-Требует-проверки.pdf'
        self.assertFalse(new.exists())
        self.assertEqual(src.read_bytes(), original)
        self.assertEqual((self.output / 'На_проверку' / '2026.05.19-Требует-проверки.pdf').read_bytes(), original)
        api.assert_called_once()

    def test_clean_names_never_need_key_or_api(self):
        make_pdf(self.input / '2024-02-29-valid.pdf')
        with patch('ai_renamer.request_suggestion') as api, redirect_stdout(StringIO()):
            result = process(self.input, self.output, rename_with_ai=True, env_file=self.root / 'absent')
        self.assertEqual(result.groups_written, 1)
        self.assertEqual(result.files_dated, 0)
        api.assert_not_called()

    def test_missing_key_stops_before_merge_and_rename(self):
        make_pdf(self.input / 'undated.pdf')
        with redirect_stderr(StringIO()), redirect_stdout(StringIO()):
            code = main(['--input-dir', str(self.input), '--output-dir', str(self.output), '--env-file', str(self.root / 'missing'), '--rename-with-ai'])
        self.assertEqual(code, 2)
        self.assertTrue((self.input / 'undated.pdf').exists())
        self.assertFalse(self.output.exists())

    def test_example_placeholder_is_not_treated_as_an_api_key(self):
        template = self.root / '.env.example'
        template.write_text(
            'PDF_MERGER_API_KEY=insert your key here\n'
            'PDF_MERGER_AI_MODEL=gpt-4o-mini\n',
            encoding='utf-8',
        )
        with self.assertRaisesRegex(RenameError, 'insert your key here'):
            load_config(template)

    def test_example_template_in_repo_contains_only_placeholder(self):
        template = Path(__file__).resolve().parent.parent / '.env.example'
        content = template.read_text(encoding='utf-8')
        self.assertIn('PDF_MERGER_API_KEY=insert your key here', content)
        self.assertIn('# PDF_MERGER_AI_MODEL=gpt-4o-mini', content)
        self.assertEqual(
            [line.strip() for line in content.splitlines()
             if line.strip().startswith('PDF_MERGER_AI_MODEL=')],
            ['PDF_MERGER_AI_MODEL=gpt-6-luna'],
        )

    def test_comment_switching_keeps_same_api_key(self):
        for active, disabled in (
            ('gpt-6-luna', 'gpt-4o-mini'),
            ('gpt-4o-mini', 'gpt-6-luna'),
        ):
            with self.subTest(model=active):
                self.env.write_text(
                    'PDF_MERGER_API_KEY=test-secret\n'
                    f'# PDF_MERGER_AI_MODEL={disabled}\n'
                    f'PDF_MERGER_AI_MODEL={active}\n',
                    encoding='utf-8',
                )
                config = load_config(self.env)
                self.assertEqual(config.api_key, 'test-secret')
                self.assertEqual(config.model, active)

    def test_two_uncommented_model_lines_fail_closed(self):
        self.env.write_text(
            'PDF_MERGER_API_KEY=test-secret\n'
            'PDF_MERGER_AI_MODEL=gpt-4o-mini\n'
            'PDF_MERGER_AI_MODEL=gpt-6-luna\n',
            encoding='utf-8',
        )
        with self.assertRaisesRegex(RenameError, 'несколько активных строк'):
            load_config(self.env)

    def test_no_model_line_defaults_to_luna(self):
        self.env.write_text('PDF_MERGER_API_KEY=test-secret\n', encoding='utf-8')
        self.assertEqual(load_config(self.env).model, 'gpt-6-luna')

    def test_chat_completions_payload_supports_luna_and_4o_mini(self):
        seen = []

        class FakeOpener:
            def open(self, request, timeout):
                seen.append(json.loads(request.data))
                response = {
                    'choices': [{
                        'message': {'content': json.dumps({
                            'date': '2026-05-19',
                            'title': 'Lab report',
                            'evidence': '19.05.2026',
                        })}
                    }]
                }
                return BytesIO(json.dumps(response).encode('utf-8'))

        with patch('ai_renamer.urllib.request.build_opener', return_value=FakeOpener()):
            for model in ('gpt-6-luna', 'gpt-4o-mini'):
                suggestion = request_suggestion(
                    AIConfig('test-secret', 'https://api.openai.com/v1/chat/completions', model),
                    'lab.pdf',
                    'Report date: 19.05.2026',
                )
                self.assertEqual(suggestion['date'], '2026-05-19')

        self.assertEqual([payload['model'] for payload in seen], ['gpt-6-luna', 'gpt-4o-mini'])
        self.assertEqual(seen[0]['reasoning_effort'], 'none')
        self.assertNotIn('reasoning_effort', seen[1])
        self.assertEqual(seen[0]['response_format'], {'type': 'json_object'})

    def test_invalid_and_fabricated_dates_rejected(self):
        with self.assertRaises(RenameError):
            _verified_date('2025-02-29', '29.02.2025', '29.02.2025')
        with self.assertRaises(RenameError):
            _verified_date('2026-05-19', '19.05.2026', 'not in document')
        with self.assertRaises(RenameError):
            _verified_date('2026-05-19', '19.05.2026', '02.04.2026')
        self.assertEqual(_verified_date('2026-05-19', '19.05.2026', 'Date: 19.05.2026'), '2026-05-19')
        self.assertEqual(_verified_date('2026-05-19', '2026/5/19', '2026/5/19'), '2026-05-19')

    def test_partial_date_rejection_keeps_bad_file_and_defers_outputs(self):
        source = [self.input / name for name in ('a.pdf', 'b.pdf', 'c.pdf')]
        for path in source:
            make_pdf(path)
        originals = [path.read_bytes() for path in source]
        output = StringIO()
        with patch('ai_renamer.extract_excerpt', return_value='Дата 19.05.2026'), \
             patch('ai_renamer.request_suggestion', side_effect=[
                 self.suggestion(), self.suggestion(day='', evidence=''), self.suggestion(),
             ]) as api, redirect_stdout(output):
            report = process(self.input, self.output, rename_with_ai=True, env_file=self.env)
        self.assertEqual((report.files_dated, report.ai_unresolved, report.groups_written), (2, 1, 0))
        self.assertEqual(api.call_count, 3)
        self.assertEqual([path.read_bytes() for path in source], originals)
        self.assertFalse(self.output.exists())
        self.assertIn('документ 2/3', output.getvalue())
        self.assertIn('b.pdf', output.getvalue())
        self.assertIn('создание итоговых PDF отложено', output.getvalue())
        self.assertEqual({p.name for p in self.input.glob('*.pdf')}, {'a.pdf', 'b.pdf', 'c.pdf'})

        # Only the one unresolved PDF needs another paid API request.
        with patch('ai_renamer.extract_excerpt', return_value='Дата 19.05.2026'), \
             patch('ai_renamer.request_suggestion', return_value=self.suggestion()) as api, \
             redirect_stdout(StringIO()):
            second = process(self.input, self.output, rename_with_ai=True, env_file=self.env)
        api.assert_called_once()
        self.assertEqual((second.files_dated, second.ai_unresolved, second.groups_written), (3, 0, 3))
        self.assertEqual(len(list((self.output / 'На_проверку').glob('*.pdf'))), 3)
        self.assertEqual(
            sorted(path.read_bytes() for path in (self.output / 'На_проверку').glob('*.pdf')),
            sorted(originals),
        )

    def test_unverified_dates_report_safe_code_but_no_filename_in_persistent_log(self):
        source = self.input / 'private-patient.pdf'
        make_pdf(source)
        events = []
        unresolved = []
        with patch('ai_renamer.extract_excerpt', return_value='Date 19.05.2026'), \
             patch('ai_renamer.request_suggestion', return_value=self.suggestion(
                 day='2026-05-20', evidence='19.05.2026',
             )):
            planned = plan_renames([source], self.input, self.env, progress=events.append, unresolved=unresolved)
        self.assertEqual(planned, {})
        self.assertEqual(len(unresolved), 1)
        self.assertEqual(unresolved[0].code, 'evidence-mismatch')
        self.assertIn('не соответствует', unresolved[0].reason.lower())
        self.assertIn('evidence-mismatch', events[-1])
        self.assertNotIn('private-patient', '\n'.join(events))
        self.assertNotIn('test-secret', '\n'.join(events))
        self.assertNotIn('19.05.2026', '\n'.join(events))
        self.assertTrue(source.exists())

    def test_api_failure_still_aborts_batch_without_partial_rename(self):
        first, second = self.input / 'a.pdf', self.input / 'b.pdf'
        make_pdf(first)
        make_pdf(second)
        with patch('ai_renamer.extract_excerpt', return_value='Date 19.05.2026'), \
             patch('ai_renamer.request_suggestion', side_effect=[
                 self.suggestion(), RenameError('AI API ответил HTTP 429: quota'),
             ]) as api:
            with self.assertRaisesRegex(RenameError, 'HTTP 429'):
                plan_renames([first, second], self.input, self.env, unresolved=[])
        self.assertEqual(api.call_count, 2)
        self.assertTrue(first.exists())
        self.assertTrue(second.exists())
        self.assertEqual(list(self.input.glob('2026-*.pdf')), [])

    def test_cli_partial_date_error_exits_nonzero_without_publishing(self):
        source = self.input / 'no-date.pdf'
        make_pdf(source)
        with patch('ai_renamer.extract_excerpt', return_value='19.05.2026'), \
             patch('ai_renamer.request_suggestion', return_value=self.suggestion(day='', evidence='')), \
             redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            code = main([
                '--input-dir', str(self.input), '--output-dir', str(self.output),
                '--env-file', str(self.env), '--rename-with-ai',
            ])
        self.assertEqual(code, 1)
        self.assertTrue(source.exists())
        self.assertFalse(self.output.exists())

    def test_two_phase_preflight_does_not_rename_on_second_error(self):
        src1 = self.input / 'scan1.pdf'
        src2 = self.input / 'scan2.pdf'
        make_pdf(src1)
        make_pdf(src2)
        with patch('ai_renamer.extract_excerpt', return_value='19.05.2026'), \
             patch('ai_renamer.request_suggestion', side_effect=[self.suggestion(), self.suggestion(day='', evidence='')]):
            with self.assertRaises(RenameError):
                plan_renames([src1, src2], self.input, self.env)
        self.assertTrue(src1.exists())
        self.assertTrue(src2.exists())

    def test_ai_progress_before_and_after_each_network_request(self):
        filenames = [self.input / 'private-client-1.pdf', self.input / 'private-client-2.pdf']
        for path in filenames:
            make_pdf(path)
        events = []
        with patch('ai_renamer.extract_excerpt', return_value='Report date: 19.05.2026'), \
             patch('ai_renamer.request_suggestion', return_value=self.suggestion()):
            planned = plan_renames(filenames, self.input, self.env, progress=events.append)
        self.assertEqual(len(planned), 2)
        self.assertIn('Документ 1/2: извлечение текста', events[0])
        self.assertTrue(any('Документ 1/2: запрос к AI' in step for step in events))
        self.assertTrue(any('Документ 1/2: ответ AI' in step for step in events))
        self.assertTrue(any('Документ 2/2: дата подтверждена' in step for step in events))
        trace = "\n".join(events)
        self.assertNotIn('private-client', trace)
        self.assertNotIn('test-secret', trace)
        self.assertNotIn('19.05.2026', trace)
        self.assertTrue(all(path.exists() for path in filenames))

    def test_scanned_pdf_without_date_fails_fast_without_api(self):
        filename = self.input / 'scan-without-text.pdf'
        make_pdf(filename)  # Synthetic blank page: no extractable text.
        events = []
        with patch('ai_renamer.request_suggestion') as api:
            with self.assertRaisesRegex(RenameError, 'нужен OCR'):
                plan_renames([filename], self.input, self.env, progress=events.append)
        api.assert_not_called()
        self.assertTrue(filename.exists())
        self.assertIn('чтение PDF', events[-1])

    def test_ai_progress_records_failing_stage_without_private_content(self):
        filename = self.input / 'patient-private-name.pdf'
        make_pdf(filename)
        events = []
        with patch('ai_renamer.extract_excerpt', return_value='Sensitive private content'), \
             patch('ai_renamer.request_suggestion', side_effect=RenameError('AI API ответил HTTP 429: secret')):
            with self.assertRaises(RenameError):
                plan_renames([filename], self.input, self.env, progress=events.append)
        self.assertIn('HTTP 429', events[-1])
        self.assertIn('ожидание API', events[-1])
        trace = "\n".join(events)
        self.assertNotIn('secret', trace)
        self.assertNotIn('patient-private-name', trace)
        self.assertNotIn('Sensitive', trace)
        self.assertTrue(filename.exists())

    def test_progress_file_is_local_without_document_identifiers(self):
        filename = self.input / 'patient-private-name.pdf'
        make_pdf(filename)
        with patch('ai_renamer.extract_excerpt', return_value='Report date 19.05.2026'), \
             patch('ai_renamer.request_suggestion', return_value=self.suggestion()), \
             patch('pdf_merger.ROOT', self.root), redirect_stdout(StringIO()):
            report = process(self.input, self.output, rename_with_ai=True, env_file=self.env)
        self.assertEqual(report.files_dated, 1)
        log = (self.root / 'logs' / 'ai-progress.log').read_text(encoding='utf-8')
        self.assertIn('Документ 1/1', log)
        self.assertIn('Исходные имена и содержимое PDF сохранены', log)
        for private in ('patient-private-name', 'test-secret', '19.05.2026', 'Lab report'):
            self.assertNotIn(private, log)

    def test_unwritable_progress_log_does_not_block_operation(self):
        trace = AIProgressLog(self.root / 'forbidden' / 'trace.log')
        with patch.object(Path, 'mkdir', side_effect=PermissionError('denied')), redirect_stdout(StringIO()) as out:
            trace('Этап обработки')
        self.assertIn('Этап обработки', out.getvalue())
        self.assertIn('прогресс остаётся в консоли', out.getvalue())

    def test_http_status_and_timeout_errors_are_actionable_not_verbose(self):
        import urllib.error
        import urllib.request

        class FakeOpener:
            def __init__(self, error):
                self.error = error
            def open(self, request, timeout):
                self.timeout = timeout
                raise self.error

        config = AIConfig('test-secret', 'https://api.openai.com/v1/chat/completions', 'gpt-6-luna')
        for code, expected in ((401, 'API-ключ'), (404, 'модель'), (429, 'квота')):
            with self.subTest(http=code):
                fake = FakeOpener(urllib.error.HTTPError(config.api_url, code, 'secret server detail', {}, None))
                with patch('ai_renamer.urllib.request.build_opener', return_value=fake):
                    with self.assertRaises(RenameError) as err:
                        request_suggestion(config, 'private-name.pdf', 'private report')
                self.assertIn(f'HTTP {code}', str(err.exception))
                self.assertIn(expected, str(err.exception))
                self.assertNotIn('secret', str(err.exception))
                self.assertEqual(fake.timeout, 30)
        with patch('ai_renamer.urllib.request.build_opener', return_value=FakeOpener(TimeoutError('secret data'))):
            with self.assertRaises(RenameError) as err:
                request_suggestion(config, 'private-name.pdf', 'private report')
        self.assertIn('тайм-аут', str(err.exception))
        self.assertNotIn('secret', str(err.exception))

    def test_ai_cannot_mislabel_radiography_as_tests(self):
        src = self.input / 'scan.pdf'
        make_pdf(src)
        with patch(
            'ai_renamer.extract_excerpt',
            return_value='Рентгенография органов грудной клетки. Дата 19.05.2026.',
        ), patch(
            'ai_renamer.request_suggestion',
            return_value=self.suggestion(title='Tests'),
        ):
            planned = plan_renames([src], self.input, self.env)
        self.assertEqual(
            next(iter(planned.values())).name,
            '2026-05-19-Рентгенография-грудной-клетки.pdf',
        )
        self.assertTrue(src.exists())

    def test_unclassifiable_ai_title_never_enters_lab_bundle(self):
        src = self.input / 'scan.pdf'
        make_pdf(src)
        with patch(
            'ai_renamer.extract_excerpt',
            return_value='Дата 19.05.2026. Других данных нет.',
        ), patch(
            'ai_renamer.request_suggestion',
            return_value=self.suggestion(title='Лабораторные исследования'),
        ):
            planned = plan_renames([src], self.input, self.env)
        self.assertEqual(
            next(iter(planned.values())).name,
            '2026-05-19-Требует-проверки.pdf',
        )
        self.assertTrue(src.exists())

    def test_collision_creates_unique_suffix_never_overwrites(self):
        existing = self.input / '2026-05-19-Требует-проверки.pdf'
        src1 = self.input / 'a.pdf'
        src2 = self.input / 'b.pdf'
        for f in (existing, src1, src2):
            make_pdf(f)
        with patch('ai_renamer.extract_excerpt', return_value='19.05.2026'), \
             patch('ai_renamer.request_suggestion', return_value=self.suggestion()):
            planned = plan_renames([src1, src2], self.input, self.env)
        self.assertEqual([p.name for p in planned.values()], ['2026-05-19-Требует-проверки-02.pdf', '2026-05-19-Требует-проверки-03.pdf'])
        self.assertTrue(existing.exists())
        self.assertTrue(src1.exists() and src2.exists())
        self.assertTrue(all(not p.exists() for p in planned.values()))

    def test_dry_run_previews_ai_groups_but_changes_nothing(self):
        src = self.input / 'scan.pdf'
        make_pdf(src)
        out = StringIO()
        with patch('ai_renamer.extract_excerpt', return_value='19.05.2026'), \
             patch('ai_renamer.request_suggestion', return_value=self.suggestion()), redirect_stdout(out):
            report = process(self.input, self.output, rename_with_ai=True, env_file=self.env, dry_run=True)
        self.assertEqual(report.groups_planned, 1)
        self.assertEqual(report.files_dated, 0)
        self.assertTrue(src.exists())
        self.assertFalse(self.output.exists())
        self.assertIn('ПЛАН AI:', out.getvalue())

    def test_check_names_is_local_and_uses_full_calendar_validation(self):
        make_pdf(self.input / '2025-02-29-invalid.pdf')
        make_pdf(self.input / '2024-02-29-valid.pdf')
        output = StringIO()
        with patch('ai_renamer.request_suggestion') as api, redirect_stdout(output):
            code = main(['--input-dir', str(self.input), '--output-dir', str(self.output), '--check-names'])
        self.assertEqual(code, 3)
        self.assertIn(': 1', output.getvalue())
        self.assertFalse(self.output.exists())
        api.assert_not_called()

    def test_opt_out_keeps_old_offline_behavior(self):
        make_pdf(self.input / 'undated.pdf')
        with patch('ai_renamer.request_suggestion') as api, redirect_stdout(StringIO()):
            report = process(self.input, self.output)
        self.assertEqual(report.files_ignored, 1)
        api.assert_not_called()

    def test_recursive_ai_includes_subdir_but_excludes_output(self):
        make_pdf(self.input / 'sub' / 'scan.pdf')
        make_pdf(self.input / 'output' / 'undated.pdf')
        with patch('ai_renamer.extract_excerpt', return_value='19.05.2026'), \
             patch('ai_renamer.request_suggestion', return_value=self.suggestion()), redirect_stdout(StringIO()):
            report = process(self.input, self.input / 'output', rename_with_ai=True, env_file=self.env, recursive=True)
        self.assertEqual(report.files_dated, 1)
        self.assertTrue((self.input / 'output' / 'undated.pdf').exists())

    def test_url_rejects_http_and_injected_credentials(self):
        self.env.write_text('PDF_MERGER_API_KEY=test-secret\nPDF_MERGER_API_URL=http://localhost/chat\n')
        with self.assertRaises(RenameError):
            load_config(self.env)
        self.env.write_text('PDF_MERGER_API_KEY=test-secret\nPDF_MERGER_API_URL=https://u:p@example.com/chat\n')
        with self.assertRaises(RenameError):
            load_config(self.env)

    def test_no_follow_redirect_for_api_key(self):
        class Redirect:
            def __init__(self):
                self.opener = None
            def open(self, request, timeout):
                self.assert_no_redirect = bool(self.opener)
                raise __import__('urllib.error', fromlist=['HTTPError']).HTTPError(request.full_url, 302, 'Redirect', {}, None)
        fake = Redirect()
        with patch('ai_renamer.urllib.request.build_opener', side_effect=lambda *handlers: fake):
            with self.assertRaises(RenameError) as error:
                request_suggestion(AIConfig('secret', 'https://api.example/chat', 'test'), 'file.pdf', '')
        self.assertIn('302', str(error.exception))

    def test_non_pdf_and_symlink_ignored(self):
        make_pdf(self.input / 'real.pdf')
        (self.input / 'notes.txt').write_text('ignore')
        try:
            (self.input / 'linked.pdf').symlink_to(self.input / 'real.pdf')
        except (OSError, NotImplementedError):
            pass
        with patch('ai_renamer.extract_excerpt', return_value='19.05.2026'), \
             patch('ai_renamer.request_suggestion', return_value=self.suggestion()), redirect_stdout(StringIO()):
            report = process(self.input, self.output, rename_with_ai=True, env_file=self.env)
        self.assertEqual(report.files_dated, 1)


if __name__ == '__main__':
    unittest.main()

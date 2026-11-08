"""Runtime configuration validation and browser-safe publication."""
import base64
from dataclasses import FrozenInstanceError
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import config


class ConfigurationTests(unittest.TestCase):
    def test_native_defaults_are_local_and_loading_does_not_create_storage(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = config.load_config({}, base_dir=root)
            self.assertEqual(settings.host, '127.0.0.1')
            self.assertEqual(settings.port, 8768)
            self.assertEqual(settings.default_engine_host, '127.0.0.1')
            self.assertEqual(settings.db_path, (root / 'data' / 'lm_benchlab_history.sqlite3').resolve())
            self.assertEqual(settings.master_key, '')
            self.assertTrue(settings.no_browser)
            self.assertFalse(settings.open_browser)
            self.assertEqual(list(root.iterdir()), [])

    def test_explicit_docker_settings_and_private_key_are_loaded(self):
        key = base64.urlsafe_b64encode(bytes(range(32))).decode('ascii')
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / 'volume' / 'history.sqlite3'
            settings = config.load_config({
                'APP_HOST': '0.0.0.0', 'APP_PORT': '9000',
                'LLM_LAB_DB': str(database), 'LLM_LAB_MASTER_KEY': key,
                'LLM_LAB_NO_BROWSER': 'true',
                'LLM_LAB_DEFAULT_ENGINE_HOST': 'host.docker.internal',
            })
            self.assertEqual(settings.host, '0.0.0.0')
            self.assertEqual(settings.port, 9000)
            self.assertEqual(settings.db_path, database.resolve())
            self.assertEqual(settings.master_key, key)
            self.assertEqual(settings.default_engine_host, 'host.docker.internal')
            self.assertNotIn(key, repr(settings))
            self.assertFalse(database.parent.exists())

    def test_public_config_exposes_only_release_and_default_engine(self):
        key = base64.urlsafe_b64encode(b'a' * 32).decode('ascii')
        settings = config.load_config({'LLM_LAB_MASTER_KEY': key,
                                       'LLM_LAB_DEFAULT_ENGINE_HOST': 'example.invalid'})
        self.assertEqual(settings.public_config(), {'name': 'LM Benchlab', 'version': config.VERSION,
                                                    'default_engine_host': 'example.invalid'})
        self.assertEqual(config.APP_NAME, 'LM Benchlab')
        self.assertEqual(config.APP_SLUG, 'lm-benchlab')
        self.assertNotIn(key, repr(settings.public_config()))
        with self.assertRaises(FrozenInstanceError):
            settings.port = 1

    def test_relative_database_path_is_independent_of_working_directory(self):
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as other:
            project = Path(directory)
            with patch('os.getcwd', return_value=other):
                settings = config.load_config({'LLM_LAB_DB': 'state/history.sqlite3'}, base_dir=project)
            self.assertEqual(settings.db_path, (project / 'state' / 'history.sqlite3').resolve())
            self.assertFalse(settings.db_path.parent.exists())

    def test_missing_environment_uses_current_environment_once(self):
        with patch.dict(os.environ, {'APP_PORT': '9234', 'APP_HOST': 'localhost'}, clear=True):
            settings = config.load_config()
            os.environ['APP_PORT'] = '9235'
            self.assertEqual(settings.port, 9234)
            self.assertEqual(settings.host, 'localhost')
            # Explicit mapping is a complete replacement, not merged with OS values.
            self.assertEqual(config.load_config({}).port, 8768)

    def test_boolean_spellings_are_explicit_and_case_insensitive(self):
        for value in ('1', 'true', 'YES', 'on'):
            with self.subTest(value=value):
                self.assertTrue(config.load_config({'LLM_LAB_NO_BROWSER': value}).no_browser)
        for value in ('0', 'false', 'NO', 'off'):
            with self.subTest(value=value):
                self.assertTrue(config.load_config({'LLM_LAB_NO_BROWSER': value}).open_browser)

    def test_invalid_ports_report_the_setting_name(self):
        for value in ('', ' ', '0', '65536', '-1', '1.2', 'abc', '12e3', '+1234', '9' * 5000):
            with self.subTest(value=value[:30]), self.assertRaisesRegex(config.ConfigurationError, 'APP_PORT'):
                config.load_config({'APP_PORT': value})

    def test_invalid_browser_flags_fail_instead_of_silently_enabling_browser(self):
        for value in ('', '2', 'maybe', 'enabled'):
            with self.subTest(value=value), self.assertRaisesRegex(config.ConfigurationError, 'LLM_LAB_NO_BROWSER'):
                config.load_config({'LLM_LAB_NO_BROWSER': value})

    def test_hosts_reject_blank_urls_ports_invalid_ips_and_unsupported_ipv6(self):
        for name in ('APP_HOST', 'LLM_LAB_DEFAULT_ENGINE_HOST'):
            for value in ('', 'http://localhost', 'localhost:8768', 'host/path', '999.999.1.2',
                          '::1', '-bad.example', 'bad..example', 'user@host', 'two hosts'):
                with self.subTest(name=name, value=value), self.assertRaisesRegex(config.ConfigurationError, name):
                    config.load_config({name: value})

    def test_database_paths_reject_invalid_files_and_do_not_create_anything(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            existing_file = root / 'file'
            existing_file.write_text('content', encoding='utf-8')
            for value in ('', '.', '..', ':memory:', 'file:test.sqlite3', 'https://host/db',
                          'name\x00.sqlite', 'bad?name.sqlite', 'folder/', 'bad:name.sqlite',
                          str(root), str(existing_file / 'history.sqlite3')):
                with self.subTest(value=value), self.assertRaisesRegex(config.ConfigurationError, 'LLM_LAB_DB'):
                    config.load_config({'LLM_LAB_DB': value}, base_dir=root)
            self.assertEqual(list(root.iterdir()), [existing_file])

    def test_invalid_master_keys_report_no_secret_value(self):
        for value in ('not-a-secret-key', 'é' * 44,
                      base64.urlsafe_b64encode(b'short').decode('ascii'),
                      base64.urlsafe_b64encode(b'a' * 32).decode('ascii') + '!'):
            with self.subTest(value=value):
                with self.assertRaisesRegex(config.ConfigurationError, 'LLM_LAB_MASTER_KEY') as raised:
                    config.load_config({'LLM_LAB_MASTER_KEY': value})
                self.assertNotIn(value, str(raised.exception))

    def test_non_string_environment_values_get_configuration_errors(self):
        for name in ('APP_HOST', 'APP_PORT', 'LLM_LAB_DB', 'LLM_LAB_MASTER_KEY',
                     'LLM_LAB_NO_BROWSER', 'LLM_LAB_DEFAULT_ENGINE_HOST'):
            with self.subTest(name=name), self.assertRaisesRegex(config.ConfigurationError, name):
                config.load_config({name: None})

    def test_import_does_not_load_environment_or_create_directories(self):
        script = ('from pathlib import Path\nfrom unittest.mock import patch\n'
                  'with patch.object(Path, "mkdir", side_effect=AssertionError("import wrote to disk")):\n'
                  '    import config\n')
        result = subprocess.run([sys.executable, '-B', '-c', script],
                                env={**os.environ, 'APP_PORT': 'malformed'},
                                cwd=Path(config.__file__).parent,
                                capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_canonical_names_match_legacy_aliases(self):
        key = base64.urlsafe_b64encode(b'b' * 32).decode('ascii')
        canonical = {
            'LM_BENCHLAB_HOST': 'localhost', 'LM_BENCHLAB_PORT': '9123',
            'LM_BENCHLAB_DB': 'custom/history.sqlite3', 'LM_BENCHLAB_MASTER_KEY': key,
            'LM_BENCHLAB_NO_BROWSER': 'false',
            'LM_BENCHLAB_DEFAULT_ENGINE_HOST': 'inference.example.invalid',
        }
        aliases = {
            'APP_HOST': canonical['LM_BENCHLAB_HOST'],
            'APP_PORT': canonical['LM_BENCHLAB_PORT'],
            'LLM_LAB_DB': canonical['LM_BENCHLAB_DB'],
            'LLM_LAB_MASTER_KEY': canonical['LM_BENCHLAB_MASTER_KEY'],
            'LLM_LAB_NO_BROWSER': canonical['LM_BENCHLAB_NO_BROWSER'],
            'LLM_LAB_DEFAULT_ENGINE_HOST': canonical['LM_BENCHLAB_DEFAULT_ENGINE_HOST'],
        }
        self.assertEqual(config.load_config(canonical), config.load_config(aliases))
        # Invalid aliases must not override or invalidate present canonical values.
        self.assertEqual(config.load_config({**{name: 'invalid' for name in aliases}, **canonical}),
                         config.load_config(canonical))

    def test_canonical_blank_values_take_precedence_and_report_canonical_names(self):
        pairs = (
            ('LM_BENCHLAB_HOST', 'APP_HOST', 'localhost'),
            ('LM_BENCHLAB_PORT', 'APP_PORT', '9123'),
            ('LM_BENCHLAB_DB', 'LLM_LAB_DB', 'custom.sqlite3'),
            ('LM_BENCHLAB_NO_BROWSER', 'LLM_LAB_NO_BROWSER', '1'),
            ('LM_BENCHLAB_DEFAULT_ENGINE_HOST', 'LLM_LAB_DEFAULT_ENGINE_HOST', 'localhost'),
        )
        for canonical, alias, value in pairs:
            with self.subTest(name=canonical), self.assertRaisesRegex(config.ConfigurationError, canonical):
                config.load_config({canonical: '', alias: value})
        key = base64.urlsafe_b64encode(b'c' * 32).decode('ascii')
        self.assertEqual(config.load_config({'LM_BENCHLAB_MASTER_KEY': '',
                                             'LLM_LAB_MASTER_KEY': key}).master_key, '')
        with self.assertRaisesRegex(config.ConfigurationError, 'LM_BENCHLAB_MASTER_KEY'):
            config.load_config({'LM_BENCHLAB_MASTER_KEY': 'invalid', 'LLM_LAB_MASTER_KEY': key})

    def test_canonical_non_string_values_report_actual_setting(self):
        for name in ('LM_BENCHLAB_HOST', 'LM_BENCHLAB_PORT', 'LM_BENCHLAB_DB',
                     'LM_BENCHLAB_MASTER_KEY', 'LM_BENCHLAB_NO_BROWSER',
                     'LM_BENCHLAB_DEFAULT_ENGINE_HOST'):
            with self.subTest(name=name), self.assertRaisesRegex(config.ConfigurationError, name):
                config.load_config({name: None})

    def test_default_database_reuses_historical_file_and_prefers_existing_new_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = root / 'data'
            data.mkdir()
            old = data / 'llm_concurrency_history.sqlite3'
            new = data / 'lm_benchlab_history.sqlite3'
            old.write_bytes(b'legacy history is preserved')
            self.assertEqual(config.load_config({}, base_dir=root).db_path, old.resolve())
            self.assertEqual(list(data.iterdir()), [old])
            # An explicit new filename starts a separate history deliberately.
            self.assertEqual(config.load_config({'LM_BENCHLAB_DB': str(new)}, base_dir=root).db_path,
                             new.resolve())
            self.assertFalse(new.exists())
            new.write_bytes(b'new history')
            self.assertEqual(config.load_config({}, base_dir=root).db_path, new.resolve())
            for name in ('LM_BENCHLAB_DB', 'LLM_LAB_DB'):
                with self.subTest(name=name):
                    self.assertEqual(config.load_config({name: str(old)}, base_dir=root).db_path,
                                     old.resolve())
            self.assertEqual(old.read_bytes(), b'legacy history is preserved')
            self.assertEqual(new.read_bytes(), b'new history')

    def test_directory_at_legacy_database_name_is_not_treated_as_old_history(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'data' / 'llm_concurrency_history.sqlite3').mkdir(parents=True)
            self.assertEqual(config.load_config({}, base_dir=root).db_path,
                             (root / 'data' / 'lm_benchlab_history.sqlite3').resolve())


if __name__ == '__main__':
    unittest.main()

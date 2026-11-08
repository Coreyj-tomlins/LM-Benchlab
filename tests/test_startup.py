"""Verify the public entry point reports configuration errors cleanly."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class StartupTests(unittest.TestCase):
    def environment(self):
        values = os.environ.copy()
        for key in ('LM_BENCHLAB_HOST', 'LM_BENCHLAB_PORT', 'LM_BENCHLAB_DB',
                    'LM_BENCHLAB_MASTER_KEY', 'LM_BENCHLAB_NO_BROWSER',
                    'LM_BENCHLAB_DEFAULT_ENGINE_HOST',
                    'APP_HOST', 'APP_PORT', 'LLM_LAB_DB', 'LLM_LAB_MASTER_KEY',
                    'LLM_LAB_NO_BROWSER', 'LLM_LAB_DEFAULT_ENGINE_HOST'):
            values.pop(key, None)
        values['PYTHONDONTWRITEBYTECODE'] = '1'
        return values

    def test_bad_port_has_a_readable_error_without_traceback(self):
        values = self.environment()
        values['LM_BENCHLAB_PORT'] = 'not-a-port'
        result = subprocess.run([sys.executable, str(ROOT / 'app.py')], env=values,
                                capture_output=True, text=True, timeout=10)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Configuration error:', result.stderr)
        self.assertIn('LM_BENCHLAB_PORT', result.stderr)
        self.assertNotIn('Traceback', result.stderr)

    def test_import_does_not_create_a_database_or_its_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'not-created' / 'history.sqlite3'
            values = self.environment()
            values['LM_BENCHLAB_DB'] = str(target)
            result = subprocess.run([sys.executable, '-c', 'import app'], cwd=ROOT,
                                    env=values, capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(target.parent.exists())


if __name__ == '__main__':
    unittest.main()

"""Category selection, reproducible workloads and history compatibility."""
import collections
import csv
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import app


MODEL_REPLY = {'choices': [{'message': {'content': 'Synthetic reply'}, 'finish_reason': 'stop'}],
               'usage': {'completion_tokens': 4, 'prompt_tokens': 12}}


def result_for(category='Coding', workload='mixed', overrides=None):
    gate = threading.Event()
    gate.set()
    with patch.object(app, 'invoke_model', return_value=MODEL_REPLY):
        return app.investigate(0, 'localhost', 1234, 'test-model', 128, 0, 15,
                               gate, workload, prompt_overrides=overrides,
                               enabled_categories=[category])


class CatalogTests(unittest.TestCase):
    def test_catalog_matches_generators_and_has_reviewable_deep_cases(self):
        catalog = app.prompt_types()
        self.assertEqual(catalog['default_enabled'], list(app.CATEGORY_IDS))
        self.assertEqual(len(catalog['categories']), 9)
        self.assertEqual(sum(c['mixed_cases'] for c in catalog['categories']), 96)
        self.assertEqual(sum(c['deep_cases'] for c in catalog['categories']), 45)
        for category in catalog['categories']:
            with self.subTest(category=category['id']):
                mixed = [app.scenario_for(i, 'mixed', [category['id']])
                         for i in range(category['mixed_cases'])]
                self.assertEqual(len({x[1] for x in mixed}), category['mixed_cases'])
                self.assertTrue(all(x[0] == category['id'] and x[2] for x in mixed))
                deep = [app.scenario_for(i, 'deep20', [category['id']])
                        for i in range(category['deep_cases'])]
                self.assertEqual(len({x[1] for x in deep}), 5)
                self.assertTrue(all(len(x[3]) >= 4 for x in deep))

    def test_selection_is_strict_and_canonical(self):
        self.assertEqual(app.normalize_categories(['Sales', 'Chat']), ['Chat', 'Sales'])
        for bad in ([], 'Chat', {}, [1], ['unknown'], ['Chat', 'Chat']):
            with self.subTest(value=bad), self.assertRaises(ValueError):
                app.normalize_categories(bad)

    def test_only_selected_categories_run_and_distribution_is_balanced(self):
        for suite in ('mixed', 'deep20'):
            for count in (1, 2, 3, 20, 499, 500):
                with self.subTest(suite=suite, count=count):
                    cases = [app.scenario_for(i, suite, ['Sales', 'Chat', 'Coding'])
                             for i in range(count)]
                    counts = collections.Counter(x[0] for x in cases)
                    self.assertLessEqual(set(counts), {'Sales', 'Chat', 'Coding'})
                    self.assertLessEqual(max(counts.values()) - min(counts.values()), 1)
                    self.assertEqual(cases, [app.scenario_for(i, suite, ['Coding', 'Sales', 'Chat'])
                                             for i in range(count)])

    def test_single_deep_category_cycles_without_falling_back_to_other_types(self):
        cases = [app.scenario_for(i, 'deep20', ['Writing']) for i in range(20)]
        self.assertEqual({x[0] for x in cases}, {'Writing'})
        self.assertEqual(cases[:5], cases[5:10])

    def test_fixed_sequential_suites_ignore_category_filter(self):
        for suite, count in (('agentic12', 12), ('chat20', 20)):
            with self.subTest(suite=suite):
                self.assertEqual([app.scenario_for(i, suite, []) for i in range(count)],
                                 [app.scenario_for(i, suite, ['Sales']) for i in range(count)])
                self.assertEqual(len({app.scenario_for(i, suite)[1] for i in range(count)}), count)

    def test_generic_chat_and_sales_do_not_require_security_services(self):
        for category in ('Chat', 'Sales'):
            cases = [app.scenario_for(i, 'deep20', [category]) for i in range(5)]
            self.assertTrue(all('MSSP' not in case[2] for case in cases))
            self.assertNotIn('MSSP', app.SYSTEM_PROMPTS[category])

    def test_defaults_preserve_deep_instructions_and_custom_overrides_work(self):
        for category in app.CATEGORY_IDS:
            with self.subTest(category=category):
                unchanged = result_for(category, 'deep20', {category: app.SYSTEM_PROMPTS[category]})
                self.assertEqual(unchanged['system_prompt'], app.DEEP_SYSTEM_PROMPTS[category])
                custom = result_for(category, 'deep20', {category: 'Use this custom instruction.'})
                self.assertEqual(custom['system_prompt'], 'Use this custom instruction.')
                self.assertTrue(custom['ok'])


class HistoryCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(app, 'DB_PATH', Path(self.temp.name) / 'history.sqlite3')
        self.db_patch.start()

    def tearDown(self):
        self.db_patch.stop()
        self.temp.cleanup()

    def test_migration_retains_old_history_and_saves_selected_types(self):
        with app.connect_db() as db:
            db.execute('''CREATE TABLE runs (
                id TEXT PRIMARY KEY, timestamp TEXT NOT NULL, engine TEXT NOT NULL,
                port INTEGER NOT NULL, model TEXT NOT NULL, concurrency INTEGER NOT NULL,
                max_tokens INTEGER NOT NULL, timeout INTEGER NOT NULL, temperature REAL NOT NULL,
                wall_seconds REAL NOT NULL, aggregate_tokens_per_second REAL,
                succeeded INTEGER NOT NULL, total INTEGER NOT NULL,
                avg_request_seconds REAL, output_tokens INTEGER NOT NULL)''')
            db.execute("INSERT INTO runs VALUES ('old','2026-01-01','Custom',1234,'old-model',1,128,15,0,1,4,1,1,1,4)")
        app.init_db()
        self.assertEqual(app.load_run('old')['model'], 'old-model')
        self.assertIsNone(app.load_run('old')['enabled_categories'])
        settings = {'host': 'localhost', 'port': 1234, 'model': 'test-model', 'concurrency': 1,
                    'max_tokens': 128, 'timeout': 15, 'temperature': 0, 'max_inflight': 1,
                    'workload': 'mixed', 'enabled_categories': ['Coding', 'Writing']}
        summary = {'run_id': 'new', 'timestamp': '2026-11-07', 'engine': 'Custom',
                   'wall_seconds': 1, 'aggregate_tokens_per_second': 4, 'succeeded': 1,
                   'total': 1, 'avg_request_seconds': 1, 'output_tokens': 4}
        result = result_for()
        result['content'] = '=HYPERLINK("https://example.invalid")'
        app.save_run(summary, [result], settings)
        saved = app.load_run('new')
        self.assertEqual(saved['enabled_categories'], ['Coding', 'Writing'])
        self.assertEqual(saved['results'][0]['system_prompt'], app.SYSTEM_PROMPTS['Coding'])
        self.assertEqual(app.load_history()[0]['enabled_categories'], ['Coding', 'Writing'])
        exported = list(csv.DictReader(io.StringIO(app.export_csv('new').decode('utf-8-sig'))))
        self.assertEqual(json.loads(exported[0]['enabled_categories']), ['Coding', 'Writing'])
        self.assertTrue(exported[0]['content'].startswith("'="))
        # Running migrations again must leave both runs intact.
        app.init_db()
        self.assertEqual(len(app.load_history()), 2)

    def test_formula_cells_are_escaped_without_changing_numeric_metrics(self):
        for value in ('=1+1', ' +SUM(A1:A2)', '-cmd', '@lookup', '\ttext', '\rtext', '\ntext'):
            with self.subTest(value=value):
                self.assertEqual(app.csv_cell(value), "'" + value)
        for value in ('plain text', 5, -2.3, None):
            self.assertEqual(app.csv_cell(value), value)


if __name__ == '__main__':
    unittest.main()

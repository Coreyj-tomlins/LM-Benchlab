"""Run complete benchmarks against a local synthetic OpenAI-compatible server."""
import csv
import http.client
import io
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import app


class FakeInference(BaseHTTPRequestHandler):
    def do_GET(self):
        self.respond({'data': [{'id': 'synthetic-model'}]})

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        with self.server.capture_lock:
            self.server.payloads.append(payload)
        self.respond({'choices': [{'message': {'content': '=SUM(1,2)'}, 'finish_reason': 'stop'}],
                      'usage': {'completion_tokens': 8, 'prompt_tokens': 32}})

    def respond(self, value):
        body = json.dumps(value).encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class BenchmarkApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.db_patch = patch.object(app, 'DB_PATH', Path(cls.temp.name) / 'history.sqlite3')
        cls.db_patch.start()
        app.init_db()
        cls.inference = ThreadingHTTPServer(('127.0.0.1', 0), FakeInference)
        cls.inference.payloads = []
        cls.inference.capture_lock = threading.Lock()
        cls.dashboard = ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
        cls.threads = []
        for server in (cls.inference, cls.dashboard):
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            cls.threads.append(thread)

    @classmethod
    def tearDownClass(cls):
        for server in (cls.inference, cls.dashboard):
            server.shutdown()
            server.server_close()
        for thread in cls.threads:
            thread.join()
        cls.db_patch.stop()
        cls.temp.cleanup()

    def request(self, path, data=None, raw=False):
        conn = http.client.HTTPConnection('127.0.0.1', self.dashboard.server_port, timeout=5)
        try:
            conn.request('POST' if data is not None else 'GET', path,
                         body=json.dumps(data) if data is not None else None,
                         headers={'Content-Type': 'application/json'} if data is not None else {})
            response = conn.getresponse()
            body = response.read()
            return response.status, body if raw else json.loads(body)
        finally:
            conn.close()

    def settings(self, categories, workload='mixed', count=4):
        return {'host': '127.0.0.1', 'port': self.inference.server_port,
                'model': 'synthetic-model', 'workload': workload, 'concurrency': count,
                'max_inflight': 2, 'max_tokens': 128, 'temperature': 0, 'timeout': 15,
                'enabled_categories': categories,
                'judge': {'enabled': False, 'host': '', 'port': '', 'model': ''}}

    def run_to_completion(self, settings):
        status, job = self.request('/api/run', settings)
        self.assertEqual(status, 202, job)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            _, progress = self.request('/api/job/' + job['run_id'])
            if progress['state'] != 'running':
                self.assertEqual(progress['state'], 'done', progress)
                break
            time.sleep(0.01)
        else:
            self.fail('Synthetic benchmark did not complete')
        status, run = self.request('/api/history/' + job['run_id'])
        self.assertEqual(status, 200)
        return run

    def test_selection_flows_through_execution_history_review_and_export(self):
        settings = self.settings(['Chat', 'Sales'])
        settings['prompt_overrides'] = {'Sales': 'Custom sales test instruction.'}
        run = self.run_to_completion(settings)
        self.assertEqual(run['enabled_categories'], ['Chat', 'Sales'])
        self.assertEqual([item['category'] for item in run['results']], ['Chat', 'Sales', 'Chat', 'Sales'])
        self.assertEqual(run['succeeded'], 4)
        self.assertEqual(run['results'][1]['system_prompt'], 'Custom sales test instruction.')
        status, review = self.request('/api/review', {'run_id': run['id'], 'index': 1, 'review': 'correct'})
        self.assertEqual(status, 200)
        self.assertEqual(review['correct'], 1)
        _, history = self.request('/api/history')
        saved = next(row for row in history['runs'] if row['id'] == run['id'])
        self.assertEqual(saved['enabled_categories'], ['Chat', 'Sales'])
        status, exported = self.request('/api/export/' + run['id'], raw=True)
        self.assertEqual(status, 200)
        rows = list(csv.DictReader(io.StringIO(exported.decode('utf-8-sig'))))
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[0]['content'], "'=SUM(1,2)")
        self.assertEqual(run['results'][0]['content'], '=SUM(1,2)')
        self.assertIn('Chat', rows[0]['enabled_categories'])

    def test_deep_suite_uses_only_new_selected_categories(self):
        run = self.run_to_completion(self.settings(['Coding', 'Analysis'], 'deep20', 20))
        self.assertEqual(run['total'], 20)
        self.assertEqual({row['category'] for row in run['results']}, {'Coding', 'Analysis'})
        self.assertTrue(all(row['review_guide'] for row in run['results']))

    def test_catalog_and_bad_selection_are_validated_before_inference(self):
        status, catalog = self.request('/api/prompt-types')
        self.assertEqual(status, 200)
        ids = {category['id'] for category in catalog['categories']}
        self.assertEqual(len(ids), 9)
        self.assertIn('Support', ids)
        for selection in ([], ['Unknown'], ['Chat', 'Chat'], 'Chat'):
            status, error = self.request('/api/run', self.settings(selection))
            self.assertEqual(status, 400, error)


if __name__ == '__main__':
    unittest.main()

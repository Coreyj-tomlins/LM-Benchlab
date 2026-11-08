"""HTTP trust-boundary checks; no external inference server is contacted."""
import http.client
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.request

import app


class DashboardSecurityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.db_patch = patch.object(app, 'DB_PATH', Path(cls.temp.name) / 'history.sqlite3')
        cls.db_patch.start()
        app.init_db()
        cls.server = app.ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()
        cls.db_patch.stop()
        cls.temp.cleanup()

    def request(self, method='GET', path='/api/history', body=None, headers=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        try:
            conn.request(method, path, body=body, headers=headers or {})
            response = conn.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            conn.close()

    def test_dashboard_and_history_are_accessible_locally(self):
        status, headers, body = self.request(path='/')
        self.assertEqual(status, 200)
        self.assertIn(app.APP_NAME.encode('utf-8'), body)
        self.assertEqual(headers['X-Content-Type-Options'], 'nosniff')
        self.assertIn("frame-ancestors 'none'", headers['Content-Security-Policy'])
        self.assertEqual(self.request()[0], 200)

    def test_public_configuration_exposes_only_ui_defaults(self):
        private = replace(app.CONFIG, master_key='synthetic-private-master-key',
                          db_path=Path(self.temp.name) / 'private-history.sqlite3')
        with patch.object(app, 'CONFIG', private):
            status, _, body = self.request(path='/api/config')
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {'name': app.APP_NAME, 'version': app.VERSION,
                                         'default_engine_host': private.default_engine_host})
        self.assertNotIn(b'synthetic-private-master-key', body)
        self.assertNotIn(b'private-history.sqlite3', body)

    def test_rebinding_host_cannot_read_history(self):
        status, _, _ = self.request(headers={'Host': f'attacker.example:{self.server.server_port}'})
        self.assertEqual(status, 403)

    def test_localhost_name_and_same_origin_are_accepted(self):
        authority = f'localhost:{self.server.server_port}'
        self.assertEqual(self.request(headers={'Host': authority, 'Origin': 'http://' + authority})[0], 200)

    def test_explicit_configured_bind_hostname_is_accepted(self):
        configured = replace(app.CONFIG, host='dashboard.example')
        authority = f'dashboard.example:{self.server.server_port}'
        with patch.object(app, 'CONFIG', configured):
            self.assertEqual(self.request(headers={'Host': authority, 'Origin': 'http://' + authority})[0], 200)

    def test_dashboard_link_from_documentation_can_open(self):
        headers = {'Sec-Fetch-Site': 'cross-site', 'Sec-Fetch-Mode': 'navigate',
                   'Sec-Fetch-Dest': 'document'}
        self.assertEqual(self.request(path='/', headers=headers)[0], 200)
        self.assertEqual(self.request(path='/api/history', headers=headers)[0], 403)

    def test_docker_can_publish_on_another_local_port(self):
        authority = 'localhost:18768'
        self.assertEqual(self.request(headers={'Host': authority, 'Origin': 'http://' + authority})[0], 200)

    def test_cross_origin_and_cross_site_are_rejected_before_proxying(self):
        with patch.object(app, 'open_endpoint') as outbound:
            for headers in ({'Origin': 'https://attacker.example'}, {'Origin': 'null'},
                            {'Sec-Fetch-Site': 'cross-site'},
                            {'Origin': 'http://127.0.0.1:1'}):
                status, _, _ = self.request(path='/api/models', headers=headers)
                self.assertEqual(status, 403)
            outbound.assert_not_called()

    def test_form_content_type_cannot_start_benchmark(self):
        self.assertEqual(self.request('POST', '/api/run', '{}', {'Content-Type': 'text/plain'})[0], 415)

    def test_invalid_json_returns_client_error(self):
        for body in ('{', '[]', 'null'):
            self.assertEqual(self.request('POST', '/api/run', body, {'Content-Type': 'application/json'})[0], 400)

    def test_model_proxy_keeps_api_key_out_of_url(self):
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self): return b'{"data":[{"id":"test-model"}]}'
        with patch.object(app, 'open_endpoint', return_value=Response()) as outbound:
            status, _, body = self.request(path='/api/models?host=127.0.0.1&port=1234', headers={'X-LLM-API-Key': 'synthetic-test-key'})
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body)['models'], ['test-model'])
            request = outbound.call_args.args[0]
            self.assertEqual(request.get_header('Authorization'), 'Bearer synthetic-test-key')
            self.assertNotIn('synthetic-test-key', request.full_url)
            self.assertNotIn(b'synthetic-test-key', body)


class EndpointRedirectTests(unittest.TestCase):
    def test_redirect_cannot_forward_credentials_to_different_origin(self):
        handler = app.SameOriginRedirectHandler()
        request = urllib.request.Request('https://inference.example/v1/models', headers={'Authorization': 'Bearer synthetic-test-key'})
        for destination in ('https://other.example/models', 'http://inference.example/models', 'https://inference.example:444/models'):
            with self.assertRaises(ValueError):
                handler.redirect_request(request, None, 302, 'Found', {}, destination)

    def test_redirect_to_same_origin_is_supported(self):
        request = urllib.request.Request('https://inference.example/v1/models', headers={'Authorization': 'Bearer synthetic-test-key'})
        redirected = app.SameOriginRedirectHandler().redirect_request(request, None, 302, 'Found', {}, 'https://inference.example/v1/models/')
        self.assertEqual(redirected.get_header('Authorization'), 'Bearer synthetic-test-key')


@unittest.skipUnless(app.Fernet, 'Install requirements.txt to test encrypted key storage')
class EncryptedKeyStorageTests(unittest.TestCase):
    def test_keys_are_encrypted_and_require_the_original_master_key(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(app, 'DB_PATH', Path(directory) / 'history.sqlite3'), \
                    patch.object(app, 'MASTER_KEY', app.Fernet.generate_key().decode('ascii')):
                app.init_db()
                app.save_engine_key('test-profile', 'synthetic-api-key')
                self.assertEqual(app.load_engine_key('test-profile'), 'synthetic-api-key')
                with app.connect_db() as db:
                    ciphertext = db.execute('SELECT encrypted_key FROM engine_keys').fetchone()[0]
                self.assertNotIn('synthetic-api-key', ciphertext)
                with patch.object(app, 'MASTER_KEY', app.Fernet.generate_key().decode('ascii')):
                    with self.assertRaisesRegex(RuntimeError, 'cannot be decrypted'):
                        app.load_engine_key('test-profile')
                app.delete_engine_key('test-profile')
                self.assertEqual(app.load_engine_key('test-profile'), '')


if __name__ == '__main__':
    unittest.main()

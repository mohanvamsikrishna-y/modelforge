from http.server import HTTPServer
from http.client import HTTPConnection
import json
import threading
import unittest
from modelforge.cli import handler_for
from modelforge.config import demo_gateway


class APITests(unittest.TestCase):
    def setUp(self):
        self.server = HTTPServer(('127.0.0.1', 0), handler_for(demo_gateway()))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.connection = HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)

    def tearDown(self):
        self.connection.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def post(self, path, data, headers=None):
        self.connection.request('POST', path, json.dumps(data), headers or {'Content-Type': 'application/json'})
        response = self.connection.getresponse()
        return response.status, response.read().decode()

    def test_complete(self):
        status, body = self.post('/v1/complete', {'prompt': 'capital'})
        self.assertEqual(status, 200)
        self.assertIn('Paris', json.loads(body)['text'])

    def test_stream_protocol(self):
        status, body = self.post('/v1/stream', {'prompt': 'capital'})
        self.assertEqual(status, 200)
        self.assertIn('event: route\n', body)
        self.assertIn('event: delta\n', body)
        self.assertIn('event: done\n', body)

    def test_stream_error_protocol(self):
        status, body = self.post('/v1/stream', {'prompt': 'capital', 'max_tokens': 1})
        self.assertEqual(status, 200)
        self.assertIn('event: error\n', body)
        self.assertNotIn('event: done\n', body)

    def test_invalid_payload(self):
        for data in [[], {'prompt': 'x', 'unknown': 1}, {'prompt': 'x', 'max_cost_usd': 'bad'}]:
            status, body = self.post('/v1/complete', data)
            self.assertEqual(status, 400)

    def test_browser_origin_rejected(self):
        status, _ = self.post('/v1/complete', {'prompt': 'x'}, {'Origin': 'https://example.com'})
        self.assertEqual(status, 403)

    def test_health(self):
        self.connection.request('GET', '/health')
        response = self.connection.getresponse()
        self.assertEqual(response.status, 200)
        self.assertEqual(json.loads(response.read()), {'status': 'ok'})

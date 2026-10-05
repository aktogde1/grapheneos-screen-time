"""Backend acceptance tests. No phone, secrets or real usage fixtures."""
import hashlib
import importlib
import json
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
from unittest.mock import patch


def backend():
    try:
        return importlib.import_module('grapheneos_screen_time.server')
    except ModuleNotFoundError as exc:
        raise AssertionError('Portable localhost server is not implemented') from exc


class ServerTests(unittest.TestCase):
    def test_cached_report_is_not_loaded_for_another_android_user(self):
        m = backend()
        with tempfile.TemporaryDirectory() as temp:
            data_dir = Path(temp)/'private'
            cached = {'app_rows': [], 'user': 0,
                      'device_key': hashlib.sha256(b'synthetic-device-a:0').hexdigest()}
            m.atomic_json(data_dir/'current-data.json', cached)
            self.assertIsNone(m.App(data_dir, user=10).load())
            self.assertEqual(json.loads((data_dir/'current-data.json').read_text()), cached)

    def test_cached_report_is_not_loaded_for_another_explicit_device(self):
        m = backend()
        with tempfile.TemporaryDirectory() as temp:
            data_dir = Path(temp)/'private'
            cached = {'app_rows': [], 'user': 0,
                      'device_key': hashlib.sha256(b'synthetic-device-a:0').hexdigest()}
            m.atomic_json(data_dir/'current-data.json', cached)
            self.assertIsNone(m.App(data_dir, serial='synthetic-device-b').load())
            self.assertEqual(json.loads((data_dir/'current-data.json').read_text()), cached)

    def test_cached_report_requires_selected_identity_metadata(self):
        m = backend()
        with tempfile.TemporaryDirectory() as temp:
            data_dir = Path(temp)/'private'
            with self.subTest(missing='user'):
                m.atomic_json(data_dir/'current-data.json', {'app_rows': []})
                self.assertIsNone(m.App(data_dir).load())
            with self.subTest(missing='device_key'):
                m.atomic_json(data_dir/'current-data.json', {'app_rows': [], 'user': 0})
                self.assertIsNone(m.App(data_dir, serial='synthetic-device-a').load())

    def test_matching_cached_snapshot_loads_offline_without_probing_a_device(self):
        m = backend()
        with tempfile.TemporaryDirectory() as temp:
            data_dir = Path(temp)/'private'
            cached = {'app_rows': [], 'user': 10, 'phone_model': 'Synthetic phone',
                      'collected_at': '2025-01-15T12:00:00+0200',
                      'device_key': hashlib.sha256(b'synthetic-device-a:10').hexdigest()}
            m.atomic_json(data_dir/'current-data.json', cached)
            for serial in (None, 'synthetic-device-a'):
                with self.subTest(serial=serial):
                    app = m.App(data_dir, serial=serial, user=10)
                    with patch.object(app, 'collect', side_effect=AssertionError('Must not probe device')):
                        self.assertEqual(app.load(), cached)
            self.assertIsNone(m.App(data_dir, serial='synthetic-device-a', user=0).load())

    def test_first_run_refresh_security_and_disconnect_preserves_data(self):
        m = backend()
        with tempfile.TemporaryDirectory() as temp:
            app = m.App(Path(temp)/'private')
            server = m.make_server(app, port=0)
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            url = f'http://127.0.0.1:{server.server_port}'
            def get(route):
                with urllib.request.urlopen(url+route,timeout=10) as r:
                    return json.load(r)
            def post(headers):
                req = urllib.request.Request(url+'/api/refresh',data=b'',headers=headers,method='POST')
                with urllib.request.urlopen(req,timeout=10) as r:
                    return json.load(r)
            try:
                self.assertEqual(get('/api/data'), {'has_data': False})
                self.assertFalse(app.data_dir.exists())
                self.assertEqual(get('/api/health')['app'], 'grapheneos-screen-time')
                token = get('/api/config')['csrf_token']
                with self.assertRaises(urllib.error.HTTPError) as blocked:
                    post({'X-Dashboard-Token':token})
                self.assertEqual(blocked.exception.code,403)
                blocked.exception.close()
                with self.assertRaises(urllib.error.HTTPError) as blocked:
                    post({'X-Dashboard-Token':token,'Origin':'https://untrusted.example'})
                self.assertEqual(blocked.exception.code,403)
                blocked.exception.close()
                expected={'app_rows': [], 'synthetic_test_fixture': True, 'user': 0,
                          'device_key': hashlib.sha256(b'synthetic-device-a:0').hexdigest()}
                with patch.object(app,'collect',return_value=expected):
                    self.assertEqual(post({'Origin':url,'X-Dashboard-Token':token})['data'],expected)
                self.assertEqual(get('/api/data'),expected)
                self.assertEqual(json.loads((app.data_dir/'current-data.json').read_text()),expected)
                with patch.object(app,'collect',side_effect=ValueError('Device disconnected')):
                    with self.assertRaises(urllib.error.HTTPError) as failed:
                        post({'Origin':url,'X-Dashboard-Token':token})
                    self.assertEqual(failed.exception.code,503)
                    failed.exception.close()
                self.assertEqual(get('/api/data'),expected)
                for selected in (m.App(app.data_dir, user=10),
                                 m.App(app.data_dir, serial='synthetic-device-b')):
                    with self.subTest(user=selected.user, serial=selected.serial):
                        server.app = selected
                        self.assertEqual(get('/api/data'), {'has_data': False})
                server.app = app
                self.assertEqual(get('/api/data'),expected)
                req=urllib.request.Request(url+'/api/config',headers={'Host':'untrusted.example'})
                with self.assertRaises(urllib.error.HTTPError) as blocked:
                    urllib.request.urlopen(req)
                self.assertEqual(blocked.exception.code,403)
                blocked.exception.close()
            finally:
                server.shutdown();server.server_close();worker.join(timeout=5)


if __name__ == '__main__':
    unittest.main()

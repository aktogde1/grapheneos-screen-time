"""Real CLI integration checks using synthetic ZIPs; no ADB or phone."""
import functools
import http.server
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
import zipfile

INSTALLER = Path(__file__).resolve().parents[1] / 'install.py'
SCRATCH = Path(os.environ.get('TMPDIR') or tempfile.gettempdir())

class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *_):
        pass

class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=SCRATCH, prefix='installer-check-')
        self.root = Path(self.temp.name)
        self.dest = self.root / 'programs' / 'dashboard'
        if sys.platform == 'darwin':
            self.data = self.root / 'home' / 'Library' / 'Application Support' / 'GrapheneOSScreenTime'
        else:
            self.data = self.root / 'user-data' / ('GrapheneOSScreenTime' if sys.platform == 'win32' else 'grapheneos-screen-time')
        self.data.mkdir(parents=True)
        self.sentinel = self.data / 'private.json'
        self.sentinel.write_text('{"synthetic": true}', encoding='utf-8')
        self.env = os.environ.copy()
        self.env['LOCALAPPDATA'] = str(self.root / 'user-data')
        self.env['XDG_DATA_HOME'] = str(self.root / 'user-data')
        self.env['HOME'] = str(self.root / 'home')
        self.env['USERPROFILE'] = str(self.root / 'home')
        handler = functools.partial(QuietHandler, directory=str(self.root))
        self.server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def archive(self, entries=None, version='v1'):
        archive = self.root / 'fixture.zip'
        content = entries or {
            'grapheneos-screen-time-main/run.py': "from pathlib import Path\nPath('launched.txt').write_text('synthetic launch')\n",
            'grapheneos-screen-time-main/README.md': version,
            'grapheneos-screen-time-main/LICENSE': 'synthetic fixture',
        }
        with zipfile.ZipFile(archive, 'w') as z:
            for name, value in content.items():
                z.writestr(name, value)
        return f'http://127.0.0.1:{self.server.server_port}/fixture.zip'

    def run_installer(self, url, *extra, dest=None, no_launch=True):
        command = [sys.executable, str(INSTALLER), '--archive-url', url,
                   '--install-dir', str(dest or self.dest)]
        if no_launch:
            command.append('--no-launch')
        return subprocess.run(command + list(extra), capture_output=True, text=True,
                              env=self.env, timeout=20)

    def test_install_synthetic_zip_without_launch(self):
        result = self.run_installer(self.archive())
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((self.dest / 'README.md').read_text(), 'v1')
        self.assertTrue((self.dest / 'run.py').is_file())
        self.assertFalse((self.dest / 'launched.txt').exists())
        self.assertEqual(self.sentinel.read_text(), '{"synthetic": true}')

    def test_printed_powershell_restart_command_quotes_literal_paths(self):
        import importlib.util
        import io
        from contextlib import redirect_stdout
        from unittest.mock import patch
        spec = importlib.util.spec_from_file_location('installer_restart', INSTALLER)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        executable = "C:/Synthetic Python's Tools/python.exe"
        target = self.root / 'programs' / "Dashboard's $data `folder"
        output = io.StringIO()
        with patch.object(module.sys, 'platform', 'win32'), \
                patch.object(module.sys, 'executable', executable), \
                patch.object(module.sys, 'argv', ['install.py', '--install-dir', str(target), '--no-launch']), \
                patch.object(module, 'install'), redirect_stdout(output):
            self.assertEqual(module.main(), 0)
        script = str(target.resolve() / 'run.py').replace("'", "''")
        self.assertIn(f"Run again (PowerShell): & 'C:/Synthetic Python''s Tools/python.exe' '{script}'",
                      output.getvalue().splitlines())

    def test_printed_posix_restart_command_quotes_literal_paths(self):
        import importlib.util
        import io
        import shlex
        from contextlib import redirect_stdout
        from unittest.mock import patch
        spec = importlib.util.spec_from_file_location('installer_restart_posix', INSTALLER)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        executable = '/opt/Synthetic Python\'s "$HOME" `tools`/python3'
        target = self.root / 'programs' / "Dashboard's $data `folder"
        for platform in ('linux', 'darwin'):
            with self.subTest(platform=platform):
                output = io.StringIO()
                with patch.object(module.sys, 'platform', platform), \
                        patch.object(module.sys, 'executable', executable), \
                        patch.object(module.sys, 'argv', ['install.py', '--install-dir', str(target), '--no-launch']), \
                        patch.object(module, 'install'), redirect_stdout(output):
                    self.assertEqual(module.main(), 0)
                line = next(line for line in output.getvalue().splitlines() if line.startswith('Run again'))
                prefix = 'Run again (POSIX shell): '
                self.assertTrue(line.startswith(prefix), line)
                self.assertEqual(shlex.split(line.removeprefix(prefix)),
                                 [executable, str(target.resolve() / 'run.py')])

    def test_reject_zip_slip_before_installing(self):
        for name in ['grapheneos-screen-time-main/../escape.py',
                     'grapheneos-screen-time-main/../../escape.py',
                     '/escape.py', 'C:/escape.py',
                     'grapheneos-screen-time-main/..\\escape.py']:
            with self.subTest(name=name):
                entries = {'grapheneos-screen-time-main/run.py': '# synthetic',
                           name: 'malicious synthetic fixture'}
                dest = self.dest.parent / ('case-' + str(abs(hash(name))))
                result = self.run_installer(self.archive(entries), dest=dest)
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertFalse(dest.exists())
                self.assertFalse((self.root / 'programs' / 'escape.py').exists())
                self.assertEqual(self.sentinel.read_text(), '{"synthetic": true}')

    def test_update_replaces_code_preserves_separate_user_data(self):
        first = self.run_installer(self.archive())
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        (self.dest / 'obsolete.py').write_text('# synthetic old code')
        update = self.run_installer(self.archive(version='v2'))
        self.assertEqual(update.returncode, 0, update.stdout + update.stderr)
        self.assertEqual((self.dest / 'README.md').read_text(), 'v2')
        self.assertFalse((self.dest / 'obsolete.py').exists())
        self.assertEqual(self.sentinel.read_text(), '{"synthetic": true}')
        bad = self.run_installer(self.archive({'root/run.py': '# synthetic',
                                               'root/../escape.py': '# bad'}))
        self.assertNotEqual(bad.returncode, 0)
        self.assertEqual((self.dest / 'README.md').read_text(), 'v2')
        self.assertEqual(self.sentinel.read_text(), '{"synthetic": true}')

    def test_reject_install_inside_user_data(self):
        result = self.run_installer(self.archive(), dest=self.data / 'installed-code')
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse((self.data / 'installed-code').exists())
        self.assertEqual(self.sentinel.read_text(), '{"synthetic": true}')

    def test_reject_file_archive_url(self):
        self.archive()
        result = self.run_installer((self.root / 'fixture.zip').as_uri())
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('HTTPS', result.stderr)
        self.assertFalse(self.dest.exists())

    def test_reject_destination_nested_in_git_checkout(self):
        checkout = self.root / 'checkout'
        (checkout / '.git').mkdir(parents=True)
        result = self.run_installer(self.archive(), dest=checkout / 'new-install')
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse((checkout / 'new-install').exists())

    def test_preserve_previous_code_if_swap_and_rollback_fail(self):
        first = self.run_installer(self.archive())
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        import importlib.util
        from unittest.mock import patch
        spec = importlib.util.spec_from_file_location('installer_under_test', INSTALLER)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        rename = Path.rename
        def failing_rename(path, destination):
            if Path(destination) == self.dest:
                raise PermissionError('synthetic failure moving new or old code into place')
            return rename(path, destination)
        with patch.dict(os.environ, self.env), patch.object(Path, 'rename', failing_rename):
            with self.assertRaises(OSError):
                module.install(self.archive(version='v2'), self.dest)
        retained = [p for p in self.dest.parent.rglob('README.md')
                    if p.read_text() == 'v1']
        self.assertEqual(len(retained), 1, 'Previous code must survive failed rollback')
        self.assertEqual(self.sentinel.read_text(), '{"synthetic": true}')

    def test_empty_environment_uses_home_defaults_on_each_platform(self):
        import importlib.util
        from unittest.mock import patch
        spec = importlib.util.spec_from_file_location('installer_defaults', INSTALLER)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        home = self.root / 'home'
        expected = {
            'win32': (home / 'AppData' / 'Local' / 'Programs' / 'GrapheneOSScreenTime',
                      home / 'AppData' / 'Local' / 'GrapheneOSScreenTime'),
            'linux': (home / '.local' / 'share' / 'grapheneos-screen-time-app',
                      home / '.local' / 'share' / 'grapheneos-screen-time'),
            'darwin': (home / 'Applications' / 'GrapheneOSScreenTime',
                       home / 'Library' / 'Application Support' / 'GrapheneOSScreenTime'),
        }
        for platform, (code, data) in expected.items():
            with self.subTest(platform=platform), patch.object(module.sys, 'platform', platform), \
                    patch.object(Path, 'home', return_value=home), \
                    patch.dict(os.environ, {'LOCALAPPDATA': '', 'XDG_DATA_HOME': ''}):
                self.assertEqual(module.default_install_dir(), code)
                self.assertEqual(module.default_data_dir(), data)
                self.assertNotEqual(code, data)

    def test_default_launch_executes_synthetic_run_py_in_code_folder(self):
        result = self.run_installer(self.archive(), no_launch=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((self.dest / 'launched.txt').read_text(), 'synthetic launch')
        self.assertEqual(self.sentinel.read_text(), '{"synthetic": true}')

    def test_reject_unmanaged_nonempty_folder_without_changes(self):
        self.dest.mkdir(parents=True)
        sentinel = self.dest / 'important.txt'
        sentinel.write_text('synthetic unrelated document')
        result = self.run_installer(self.archive())
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(sentinel.read_text(), 'synthetic unrelated document')
        self.assertFalse((self.dest / 'run.py').exists())

    def test_reject_ancestor_of_data_even_with_install_marker(self):
        target = self.data.parent
        (target / '.grapheneos-screen-time-install.json').write_text(
            json.dumps({'app': 'grapheneos-screen-time', 'installer_schema': 1}))
        result = self.run_installer(self.archive(), dest=target)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.sentinel.read_text(), '{"synthetic": true}')

    def test_reject_symlink_and_windows_alias_zip_members(self):
        import stat
        symlink = zipfile.ZipInfo('root/link')
        symlink.create_system = 3
        symlink.external_attr = (stat.S_IFLNK | 0o777) << 16
        extras = [{symlink: '../../outside'},
                  {'root/NUL.txt': 'bad'}, {'root/file:stream': 'bad'},
                  {'root/file.': 'bad'}, {'root/file ': 'bad'},
                  {'root/FILE.py': 'one', 'root/file.py': 'two'}]
        for index, extra in enumerate(extras):
            with self.subTest(index=index):
                entries = {'root/run.py': '# synthetic', **extra}
                dest = self.dest.parent / f'bad-entry-{index}'
                result = self.run_installer(self.archive(entries), dest=dest)
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertFalse(dest.exists())

    def test_reject_non_loopback_http_before_download(self):
        result = self.run_installer('http://example.invalid/source.zip')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('HTTPS', result.stderr)
        self.assertFalse(self.dest.exists())

    def test_bad_archive_preserves_installed_code(self):
        first = self.run_installer(self.archive())
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        url = self.archive()
        (self.root / 'fixture.zip').write_bytes(b'not a zip: synthetic fixture')
        result = self.run_installer(url)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((self.dest / 'README.md').read_text(), 'v1')
        self.assertEqual(self.sentinel.read_text(), '{"synthetic": true}')

    def test_missing_run_py_is_not_installed(self):
        result = self.run_installer(self.archive({'root/README.md': 'synthetic'}))
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.dest.exists())

if __name__ == '__main__':
    unittest.main(verbosity=2)

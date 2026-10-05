"""Loopback-only dashboard HTTP API. Python standard library only."""
import argparse
import datetime as dt
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import os
from pathlib import Path, PurePosixPath
import secrets
import subprocess
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
import webbrowser

WEB = Path(__file__).resolve().parent.parent/'web'
APP_ID = 'grapheneos-screen-time'


def default_data_dir():
    if sys.platform == 'win32':
        return Path(os.environ.get('LOCALAPPDATA', Path.home()/'AppData'/'Local'))/'GrapheneOSScreenTime'
    if sys.platform == 'darwin':
        return Path.home()/'Library'/'Application Support'/'GrapheneOSScreenTime'
    return Path(os.environ.get('XDG_DATA_HOME', Path.home()/'.local'/'share'))/APP_ID


def atomic_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name+'.tmp-'+secrets.token_hex(8))
    try:
        with temp.open('x', encoding='utf-8') as handle:
            if os.name != 'nt':
                os.chmod(temp, 0o600)
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


class App:
    def __init__(self, data_dir=None, adb=None, serial=None, user=0):
        if user < 0:
            raise ValueError('Android user must be nonnegative')
        self.data_dir = Path(data_dir) if data_dir is not None else default_data_dir()
        self.adb, self.serial, self.user = adb, serial, user
        self.token = secrets.token_urlsafe(32)
        self.refresh_lock = threading.Lock()

    def load(self):
        try:
            data = json.loads((self.data_dir/'current-data.json').read_text(encoding='utf-8'))
            if not isinstance(data, dict) or not isinstance(data.get('app_rows'), list):
                return None
            if data.get('user') != self.user:
                return None
            if self.serial is not None:
                key = hashlib.sha256((self.serial+':'+str(self.user)).encode('utf-8')).hexdigest()
                if data.get('device_key') != key:
                    return None
            return data
        except (OSError, ValueError):
            return None

    def collect(self):
        from .adb import read_phone
        return read_phone(self)

    def refresh(self):
        report = self.collect()
        stamp = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
        atomic_json(self.data_dir/'snapshots'/(stamp+'.json'), report)
        atomic_json(self.data_dir/'current-data.json', report)
        return report

    def identity(self):
        value = json.dumps([str(self.data_dir.resolve()),self.adb,self.serial,self.user])
        return hashlib.sha256(value.encode()).hexdigest()


class Handler(BaseHTTPRequestHandler):
    def respond(self, status, data, content_type='application/json; charset=utf-8'):
        body = json.dumps(data, ensure_ascii=False).encode('utf-8') if isinstance(data, dict) else data
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
        self.end_headers()
        self.wfile.write(body)

    def valid_host(self):
        return self.headers.get('Host') in {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}

    def do_GET(self):
        if not self.valid_host():
            return self.respond(403, {'error': 'Invalid host'})
        app = self.server.app
        route = urllib.parse.urlsplit(self.path).path
        if route == '/api/config':
            return self.respond(200, {'csrf_token': app.token})
        if route == '/api/data':
            return self.respond(200, app.load() or {'has_data': False})
        if route == '/api/health':
            return self.respond(200, {'ok': True,'app': APP_ID,'read_only_adb': True,'bind':'127.0.0.1','identity':app.identity()})
        path = urllib.parse.unquote(route)
        if path == '/':
            path = '/index.html'
        relative = PurePosixPath(path.lstrip('/'))
        if '..' in relative.parts or '\\' in path or relative.suffix not in {'.html','.js','.css','.svg'}:
            return self.respond(404, {'error':'Not found'})
        target = WEB.joinpath(*relative.parts).resolve()
        if not target.is_relative_to(WEB.resolve()) or not target.is_file():
            return self.respond(404, {'error':'Not found'})
        try:
            return self.respond(200, target.read_bytes(), (mimetypes.guess_type(target.name)[0] or 'text/plain')+'; charset=utf-8')
        except OSError:
            return self.respond(503, {'error':'Cannot read dashboard file'})

    def do_POST(self):
        app = self.server.app
        origin = self.headers.get('Origin')
        if not self.valid_host() or origin != f"http://{self.headers.get('Host')}" or not secrets.compare_digest(self.headers.get('X-Dashboard-Token',''), app.token):
            return self.respond(403, {'error':'Запрос разрешён только с локального дашборда.'})
        if self.path != '/api/refresh':
            return self.respond(404, {'error':'Not found'})
        if not app.refresh_lock.acquire(blocking=False):
            return self.respond(409, {'error':'Обновление уже идёт. Подождите.'})
        try:
            return self.respond(200, {'ok':True,'data':app.refresh()})
        except (ValueError, OSError, subprocess.TimeoutExpired) as exc:
            return self.respond(503, {'error':str(exc),'previous_data_preserved':True})
        finally:
            app.refresh_lock.release()

    def log_message(self, fmt, *args):
        pass


def make_server(app, port=8797):
    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    server.daemon_threads = True
    server.app = app
    return server


def main(argv=None):
    parser = argparse.ArgumentParser(description='Screen Time for GrapheneOS: local, read-only ADB dashboard')
    parser.add_argument('--adb',help='Path to an existing adb executable')
    parser.add_argument('--serial',help='ADB device serial when multiple devices are connected')
    parser.add_argument('--user',type=int,default=0,help='Android profile id (default: owner/0)')
    parser.add_argument('--port',type=int,default=8797)
    parser.add_argument('--data-dir',type=Path,help='Local private data directory (default: OS user data directory)')
    parser.add_argument('--no-browser',action='store_true')
    args = parser.parse_args(argv)
    if not 0 <= args.port <= 65535 or args.user < 0:
        parser.error('Invalid port or Android profile id')
    app = App(args.data_dir,args.adb,args.serial,args.user)
    try:
        server = make_server(app,args.port)
    except OSError:
        if args.port:
            try:
                with urllib.request.urlopen(f'http://127.0.0.1:{args.port}/api/health',timeout=2) as reply:
                    health = json.load(reply)
                if health.get('app') == APP_ID and health.get('identity') == app.identity():
                    url = f'http://127.0.0.1:{args.port}/'
                    print(f'Dashboard already running: {url}',flush=True)
                    if not args.no_browser:
                        webbrowser.open(url)
                    return 0
            except (OSError,ValueError,urllib.error.URLError):
                pass
        server = make_server(app,0)
    url = f'http://127.0.0.1:{server.server_port}/'
    print(f'Dashboard ready: {url}',flush=True)
    print('Local only. Refresh reads ADB; Ctrl+C stops the server.',flush=True)
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0

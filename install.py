#!/usr/bin/env python3
"""Install the dashboard's source only, using Python's standard library."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import shlex
import subprocess
import stat
import sys
import tempfile
import urllib.parse
import urllib.request
import urllib.error
import zipfile
import uuid

ARCHIVE_URL = 'https://github.com/aktogde1/grapheneos-screen-time/archive/refs/heads/main.zip'


def default_install_dir() -> Path:
    if sys.platform == 'win32':
        return Path(os.environ.get('LOCALAPPDATA') or Path.home() / 'AppData' / 'Local') / 'Programs' / 'GrapheneOSScreenTime'
    if sys.platform == 'darwin':
        return Path.home() / 'Applications' / 'GrapheneOSScreenTime'
    return Path(os.environ.get('XDG_DATA_HOME') or Path.home() / '.local' / 'share') / 'grapheneos-screen-time-app'


def extract_source(archive: Path, destination: Path) -> Path:
    """Validate every member before writing; never use extractall on remote ZIPs."""
    reserved = {'CON', 'PRN', 'AUX', 'NUL', 'CONIN$', 'CONOUT$'}
    reserved.update(f'{prefix}{n}' for prefix in ('COM', 'LPT') for n in range(1, 10))
    with zipfile.ZipFile(archive) as source:
        entries = source.infolist()
        if len(entries) > 10000 or sum(e.file_size for e in entries) > 128 * 1024 * 1024:
            raise ValueError('Archive exceeds extraction limits')
        seen = set()
        roots = set()
        for entry in entries:
            name = entry.filename
            parts = name.rstrip('/').split('/')
            if (entry.orig_filename != name or name.startswith('/') or '\\' in name
                    or any(p in ('', '.', '..') or p.endswith((' ', '.'))
                           or any(ord(c) < 32 or c in ':<>"|?*' for c in p)
                           or p.split('.')[0].upper() in reserved for p in parts)):
                raise ValueError(f'Unsafe ZIP path: {name!r}')
            mode = stat.S_IFMT(entry.external_attr >> 16)
            if mode not in (0, stat.S_IFREG, stat.S_IFDIR) or entry.flag_bits & 1:
                raise ValueError(f'Unsupported ZIP entry (link/special/encrypted): {name!r}')
            key = '/'.join(parts).casefold()
            if key in seen:
                raise ValueError(f'Duplicate ZIP path: {name!r}')
            seen.add(key)
            roots.add(parts[0])
        if len(roots) != 1:
            raise ValueError('Archive must contain exactly one repository root')
        root = next(iter(roots))
        if f'{root}/run.py'.casefold() not in seen:
            raise ValueError('Archive repository root must contain run.py')
        for entry in entries:
            output = destination.joinpath(*entry.filename.rstrip('/').split('/'))
            if entry.is_dir():
                output.mkdir(parents=True, exist_ok=True)
            else:
                output.parent.mkdir(parents=True, exist_ok=True)
                with source.open(entry) as incoming, output.open('xb') as outgoing:
                    shutil.copyfileobj(incoming, outgoing)
        repository = destination / root
        if not (repository / 'run.py').is_file():
            raise ValueError('Archive must contain a regular run.py file')
        return repository


MARKER = '.grapheneos-screen-time-install.json'
OWNER = {'app': 'grapheneos-screen-time', 'installer_schema': 1}


def default_data_dir() -> Path:
    if sys.platform == 'win32':
        return Path(os.environ.get('LOCALAPPDATA') or Path.home() / 'AppData' / 'Local') / 'GrapheneOSScreenTime'
    if sys.platform == 'darwin':
        return Path.home() / 'Library' / 'Application Support' / 'GrapheneOSScreenTime'
    return Path(os.environ.get('XDG_DATA_HOME') or Path.home() / '.local' / 'share') / 'grapheneos-screen-time'


def validate_target(target: Path) -> None:
    data = default_data_dir().resolve()
    if target == data or target in data.parents or data in target.parents:
        raise ValueError('App code must not overlap the private user-data directory')
    if target == Path.home().resolve() or target == Path(target.anchor):
        raise ValueError('Refusing to replace a home directory or filesystem root')
    if any((ancestor / '.git').exists() for ancestor in (target, *target.parents)):
        raise ValueError('Install app code outside Git checkouts')
    if target.exists():
        if not target.is_dir():
            raise ValueError('Install target is not a directory')
        if any(target.iterdir()):
            marker = target / MARKER
            try:
                owned = json.loads(marker.read_text(encoding='utf-8')) == OWNER
            except (OSError, ValueError):
                owned = False
            if not owned:
                raise ValueError('Refusing to replace a nonempty folder not managed by this installer')


def validate_url(url: str) -> None:
    parsed = urllib.parse.urlsplit(url)
    local_test = parsed.scheme == 'http' and parsed.hostname in ('127.0.0.1', '::1', 'localhost')
    if ((parsed.scheme != 'https' and not local_test) or not parsed.hostname
            or parsed.username or parsed.password or parsed.fragment):
        raise ValueError('Archive URL must use HTTPS (HTTP loopback is allowed only for local tests)')


class SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_url(newurl)
        if urllib.parse.urlsplit(req.full_url).scheme == 'https' and urllib.parse.urlsplit(newurl).scheme != 'https':
            raise ValueError('Refusing HTTPS downgrade in archive redirect')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download_archive(url: str, archive: Path) -> None:
    validate_url(url)
    opener = urllib.request.build_opener(SafeRedirectHandler())
    request = urllib.request.Request(url, headers={'User-Agent': 'grapheneos-screen-time-installer/1'})
    limit = 32 * 1024 * 1024
    with opener.open(request, timeout=30) as incoming, archive.open('xb') as outgoing:
        total = 0
        while chunk := incoming.read(64 * 1024):
            total += len(chunk)
            if total > limit:
                raise ValueError('Archive exceeds the 32 MiB download limit')
            outgoing.write(chunk)


def install(url: str, target: Path) -> None:
    validate_url(url)
    validate_target(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.screen-time-install-', dir=target.parent) as work:
        work = Path(work)
        archive = work / 'source.zip'
        download_archive(url, archive)
        repository = extract_source(archive, work / 'extracted')
        (repository / MARKER).write_text(json.dumps(OWNER) + '\n', encoding='utf-8')
        # Validate again before the swap: extraction/network failures leave old code intact.
        validate_target(target)
        # Keep recovery code outside the temporary tree so failed rollback cannot delete it.
        backup = target.parent / f'.{target.name}.previous-{uuid.uuid4().hex}'
        existed = target.exists()
        if existed:
            target.rename(backup)
        try:
            repository.rename(target)
        except OSError:
            if existed:
                try:
                    backup.rename(target)
                except OSError as recovery_error:
                    raise OSError(f'Swap and rollback failed; previous code is preserved at {backup}') from recovery_error
            raise
        if existed:
            try:
                shutil.rmtree(backup)
            except OSError:
                print(f'Warning: updated code installed, but old code remains at {backup}', file=sys.stderr)


def main() -> int:
    if sys.version_info < (3, 10):
        print('Python 3.10 or newer is required. Install Python separately.', file=sys.stderr)
        return 1
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--install-dir', type=Path, default=default_install_dir(),
                        help='Separate app-code directory; never use your private data folder')
    parser.add_argument('--archive-url', default=ARCHIVE_URL,
                        help='Trusted repository ZIP over HTTPS (HTTP loopback for tests only)')
    parser.add_argument('--no-launch', action='store_true',
                        help='Install/update source without starting run.py or accessing ADB')
    args = parser.parse_args()
    try:
        target = args.install_dir.expanduser().resolve()
        print(f'App-code directory: {target}', flush=True)
        print('Installing source only; Python and ADB must already be installed.', flush=True)
        print('Updates replace ALL local files in a managed app-code directory, not external data.', flush=True)
        install(args.archive_url, target)
        print(f'Installed dashboard source: {target}')
        if sys.platform == 'win32':
            executable = sys.executable.replace("'", "''")
            script = str(target / 'run.py').replace("'", "''")
            print(f"Run again (PowerShell): & '{executable}' '{script}'")
        else:
            command = shlex.join([sys.executable, str(target / 'run.py')])
            print(f'Run again (POSIX shell): {command}')
        if not args.no_launch:
            return subprocess.call([sys.executable, str(target / 'run.py')], cwd=target)
        return 0
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        print(f'Installation failed: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())

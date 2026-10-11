"""Local stage assistant: python -m eventyay.hackathon.companion --url <station-state URL>."""

import argparse
import getpass
import hashlib
import ipaddress
import json
import logging
import os
import re
import shutil
import signal
import stat
import subprocess
import tempfile
import threading
import time
import uuid
import zipfile
from pathlib import Path, PurePosixPath
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


logger = logging.getLogger('hackathon.station')
MAX_FILE_SIZE = 50 * 1024 * 1024


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise HTTPError(req.full_url, code, 'Redirect refused for authenticated station request', headers, fp)


def validate_manifest(data):
    if not isinstance(data, dict) or not isinstance(data.get('clock'), dict):
        raise ValueError('Invalid station manifest.')
    clock = data['clock']
    if clock.get('phase') not in {'idle', 'demo', 'paused', 'transition', 'finished'}:
        raise ValueError('Invalid clock phase.')
    if not isinstance(clock.get('remaining'), int) or not 0 <= clock['remaining'] <= 1800:
        raise ValueError('Invalid remaining time.')
    current = data.get('current')
    if current is not None:
        if not isinstance(current, dict) or not isinstance(current.get('id'), int):
            raise ValueError('Invalid current slot.')
        uuid.UUID(current.get('project_id', ''))
    assets = data.get('assets')
    if not isinstance(assets, list) or len(assets) > 10000:
        raise ValueError('Invalid asset list.')
    for asset in assets:
        if not isinstance(asset, dict):
            raise ValueError('Invalid asset.')
        uuid.UUID(asset.get('id', ''))
        uuid.UUID(asset.get('project_id', ''))
        if asset.get('kind') not in {'slides', 'poster', 'video', 'source'}:
            raise ValueError('Invalid asset type.')
        if not isinstance(asset.get('filename'), str):
            raise ValueError('Invalid filename.')
        if not isinstance(asset.get('size'), int) or not 0 < asset['size'] <= MAX_FILE_SIZE:
            raise ValueError('Invalid asset size.')
        if not isinstance(asset.get('sha256'), str) or not re.fullmatch('[a-f0-9]{64}', asset['sha256']):
            raise ValueError('Invalid asset checksum.')
        if not isinstance(asset.get('url'), str) or not asset['url'].startswith('/'):
            raise ValueError('Invalid asset URL.')
    return data


def safe_extract(source: Path, destination: Path):
    destination.mkdir(parents=True, exist_ok=True)
    destination = destination.resolve()
    with zipfile.ZipFile(source) as archive:
        entries = archive.infolist()
        if len(entries) > 2000 or sum(entry.file_size for entry in entries) > 200 * 1024 * 1024:
            raise ValueError('Archive is too large.')
        for entry in entries:
            path = PurePosixPath(entry.filename)
            mode = entry.external_attr >> 16
            target = destination.joinpath(*path.parts).resolve()
            if (
                path.is_absolute()
                or '..' in path.parts
                or '\\' in entry.filename
                or not target.is_relative_to(destination)
                or stat.S_ISLNK(mode)
            ):
                raise ValueError('Unsafe archive member.')
        for entry in entries:
            target = destination.joinpath(*PurePosixPath(entry.filename).parts)
            if entry.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(entry) as incoming, target.open('wb') as outgoing:
                    shutil.copyfileobj(incoming, outgoing, length=1024 * 1024)


class Station:
    def __init__(self, url, token, cache, execute=False, office='libreoffice', unpack=False):
        parsed = urlsplit(url)
        local = parsed.hostname == 'localhost'
        try:
            local = local or ipaddress.ip_address(parsed.hostname or '').is_loopback
        except ValueError:
            pass
        if parsed.scheme != 'https' and not (parsed.scheme == 'http' and local):
            raise ValueError('Use HTTPS, or HTTP only on a loopback host.')
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError('Station URL must not contain credentials, a query, or a fragment.')
        self.url, self.token, self.cache = url, token, cache.resolve()
        self.execute, self.office, self.unpack = execute, office, unpack
        self.opener = build_opener(NoRedirect())
        self.process = None
        self.process_lock = threading.RLock()
        self.cutoff_timer = None
        self.current_key = None
        self.deadline = None
        self.last_sync = time.monotonic()
        self.cache.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.profile = Path(tempfile.mkdtemp(prefix='office-', dir=self.cache))

    def request(self, url, timeout=10):
        if urlsplit(url)[:2] != urlsplit(self.url)[:2]:
            raise ValueError('Refusing to send the station token to another origin.')
        return self.opener.open(Request(url, headers={'Authorization': f'Bearer {self.token}'}), timeout=timeout)

    def fetch(self):
        with self.request(self.url) as response:
            body = response.read(5 * 1024 * 1024 + 1)
        if len(body) > 5 * 1024 * 1024:
            raise ValueError('Station manifest is too large.')
        return validate_manifest(json.loads(body))

    def download(self, asset):
        suffix = Path(asset['filename']).suffix.lower()
        if suffix not in {'.pdf', '.pptx', '.png', '.jpg', '.jpeg', '.webp', '.mp4', '.webm', '.zip'}:
            raise ValueError('Unsupported asset extension.')
        destination = self.cache / f'{asset["id"]}-{asset["sha256"][:12]}{suffix}'
        if destination.exists():
            with destination.open('rb') as existing:
                if hashlib.file_digest(existing, 'sha256').hexdigest() == asset['sha256']:
                    return destination
        temporary = destination.with_suffix('.partial')
        try:
            digest, total = hashlib.sha256(), 0
            with self.request(urljoin(self.url, asset['url']), timeout=30) as response, temporary.open('wb') as output:
                while chunk := response.read(1024 * 1024):
                    total += len(chunk)
                    if total > min(MAX_FILE_SIZE, asset['size']):
                        raise ValueError('Download exceeded its declared size.')
                    digest.update(chunk)
                    output.write(chunk)
            if total != asset['size'] or digest.hexdigest() != asset['sha256']:
                raise ValueError('Download checksum or size mismatch.')
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)
        logger.info('Cached %s (%s bytes)', asset['filename'], total)
        if self.unpack and suffix == '.zip':
            safe_extract(destination, destination.with_suffix('.unpacked'))
        return destination

    def stop(self):
        with self.process_lock:
            self.stop_process()

    def stop_process(self):
        if self.process and self.process.poll() is None:
            if os.name == 'nt':
                subprocess.run(
                    ['taskkill', '/PID', str(self.process.pid), '/T', '/F'],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            else:
                try:
                    os.killpg(self.process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                if os.name != 'nt':
                    try:
                        os.killpg(self.process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                self.process.wait(timeout=3)
            logger.info('Closed the presentation started by this assistant.')
        self.process = None

    def open(self, asset, path):
        logger.info('Presentation: %s', asset['filename'])
        if self.execute:
            with self.process_lock:
                if self.deadline is not None and time.monotonic() >= self.deadline:
                    return
                self.process = subprocess.Popen(
                    [self.office, f'-env:UserInstallation={self.profile.as_uri()}', '--norestore', '--show', str(path)],
                    shell=False,
                    start_new_session=os.name != 'nt',
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
        else:
            logger.info('Dry run: use --execute after rehearsing on the venue computer.')

    def update(self, data):
        self.last_sync = time.monotonic()
        current, clock = data['current'], data['clock']
        key = current['id'] if current else None
        if key != self.current_key or clock['phase'] in {'idle', 'transition', 'finished'}:
            self.stop()
        if clock['phase'] == 'demo':
            self.deadline = self.last_sync + clock['remaining']
        elif clock['phase'] == 'paused':
            self.deadline = None
        else:
            self.deadline = self.last_sync
        if self.cutoff_timer:
            self.cutoff_timer.cancel()
        if self.deadline is not None:
            self.cutoff_timer = threading.Timer(max(0, self.deadline - time.monotonic()), self.enforce_cutoff)
            self.cutoff_timer.daemon = True
            self.cutoff_timer.start()
        should_start = key is not None and key != self.current_key and clock['phase'] == 'demo'
        self.current_key = key
        if should_start:
            candidates = [
                a for a in data['assets'] if a['project_id'] == current['project_id'] and a['kind'] == 'slides'
            ]
            if candidates:
                path = self.download(candidates[0])
                if time.monotonic() < self.deadline:
                    self.open(candidates[0], path)
            else:
                logger.info('No slides for this project; use its live demo or cached backup video.')
        if clock['phase'] == 'idle':
            for asset in data['assets']:
                self.download(asset)
        remaining = max(0, int(self.deadline - time.monotonic())) if self.deadline else clock['remaining']
        print(f'\r{clock["phase"]:12s} {remaining // 60:02d}:{remaining % 60:02d} ', end='', flush=True)

    def enforce_cutoff(self):
        if self.deadline is not None and time.monotonic() >= self.deadline:
            self.stop()
        if time.monotonic() - self.last_sync > 10:
            self.stop()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', required=True)
    parser.add_argument('--cache', type=Path, default=Path.home() / '.cache' / 'eventyay-stage')
    parser.add_argument(
        '--execute', action='store_true', help='Actually open and stop the dedicated LibreOffice process.'
    )
    parser.add_argument('--office', default='libreoffice', help='Path to the LibreOffice executable.')
    parser.add_argument(
        '--unpack', action='store_true', help='Safely unpack downloaded ZIP assets; never execute them.'
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    token = os.environ.get('HACKATHON_STATION_TOKEN') or getpass.getpass('12-hour station token: ')
    station = Station(args.url, token, args.cache, args.execute, args.office, args.unpack)
    try:
        while True:
            try:
                station.update(station.fetch())
            except (HTTPError, URLError, TimeoutError, OSError, ValueError, zipfile.BadZipFile) as error:
                logger.warning('Station synchronization failed: %s', error)
                if isinstance(error, HTTPError) and error.code in {401, 403}:
                    break
            station.enforce_cutoff()
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info('Stopping station assistant.')
    finally:
        if station.cutoff_timer:
            station.cutoff_timer.cancel()
        station.stop()
        shutil.rmtree(station.profile)


if __name__ == '__main__':
    main()

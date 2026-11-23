"""Serve the daily release notice over a private Unix socket."""

from contextlib import closing
from datetime import datetime, timezone
import hashlib
from http.client import HTTPException
from http.server import BaseHTTPRequestHandler
import json
import os
from pathlib import Path
import re
from socketserver import UnixStreamServer
import sqlite3
from string import Template
from threading import Thread
import time
import urllib.request


def release_version(tag):
    match = re.fullmatch(r'v(\d+)\.(\d+)\.(\d+)(?:-([1-9]\d*))?', tag)
    if match:
        return tuple(int(part or '0') for part in match.groups())


def newer_release(repository, current):
    request = urllib.request.Request(
        f'https://api.github.com/repos/{repository}/releases/latest',
        headers={'Accept': 'application/vnd.github+json', 'User-Agent': 'code-server-aio-update-notice'})
    with urllib.request.urlopen(request, timeout=5) as response:
        release = json.loads(response.read(1024 * 1024))
    tag = release.get('tag_name', '')
    version = release_version(tag)
    assets = {asset.get('name') for asset in release.get('assets', [])}
    if (version and version > current and not (release.get('draft') or release.get('prerelease'))
            and {'SHA256SUMS', 'code-server-aio.tar.gz'} <= assets):
        return tag


class Notice:
    def __init__(self, database, page, repository, current):
        self.database = database
        self.page = page
        self.repository = repository
        self.current = current
        self.latest = None
        self.database.execute('CREATE TABLE IF NOT EXISTS seen (subject TEXT PRIMARY KEY, day TEXT NOT NULL)')

    def refresh(self):
        while True:
            try:
                self.latest = newer_release(self.repository, release_version(self.current))
                delay = 3600
            except (OSError, HTTPException, ValueError, TypeError, AttributeError):
                self.latest = None
                delay = 300
            time.sleep(delay)

    def for_subject(self, subject, day):
        latest = self.latest
        if not latest:
            return None
        # One dispatcher shares this record across both Envoy workers and all browsers.
        subject_hash = hashlib.sha256(subject).hexdigest()
        with self.database:
            self.database.execute('DELETE FROM seen WHERE day < ?', (day,))
            new = self.database.execute(
                'INSERT OR IGNORE INTO seen (subject, day) VALUES (?, ?)', (subject_hash, day))
        if new.rowcount:
            url = f'https://github.com/{self.repository}/releases/tag/{latest}'
            return self.page.substitute(current=self.current, latest=latest, release_url=url).encode()


class NoticeHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def do_POST(self):
        length = self.headers.get('Content-Length', '')
        if self.path != '/' or not length.isdecimal() or len(length) > 4 or not 0 < int(length) <= 1024:
            self.send_error(400)
            return
        subject = self.rfile.read(int(length))
        day = datetime.now(timezone.utc).date().isoformat()
        page = self.server.notice.for_subject(subject, day)
        self.send_response(200 if page else 204)
        if page:
            self.send_header('Content-Length', str(len(page)))
        self.end_headers()
        if page:
            self.wfile.write(page)


def main():
    repository = os.environ.get('AIO_RELEASE_REPOSITORY', '')
    current = os.environ.get('AIO_RELEASE_TAG', '')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*', repository):
        return
    if not release_version(current):
        return
    os.umask(0o077)
    socket = Path('/run/envoy/update-notice.sock')
    socket.unlink(missing_ok=True)
    with UnixStreamServer(str(socket), NoticeHandler) as server:
        page = Template(Path('/etc/envoy/pages/update-notice.html').read_text())
        with closing(sqlite3.connect('/run/envoy/update-notice.sqlite3')) as database:
            server.notice = Notice(database, page, repository, current)
            Thread(target=server.notice.refresh, daemon=True).start()
            server.serve_forever()


if __name__ == '__main__':
    main()

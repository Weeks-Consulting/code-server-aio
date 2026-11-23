"""Serve the daily release notice over a private Unix socket."""

from datetime import datetime, timezone
from http.client import HTTPException
from http.server import BaseHTTPRequestHandler
import json
import logging
import os
from pathlib import Path
import re
from socketserver import UnixStreamServer
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
    def __init__(self, page, repository, current):
        self.page = page
        self.repository = repository
        self.current = current
        self.latest = None
        self.check_failed = False

    def refresh(self):
        while True:
            time.sleep(self.check_for_update())

    def check_for_update(self):
        try:
            self.latest = newer_release(self.repository, release_version(self.current))
        except (OSError, HTTPException, ValueError, TypeError, AttributeError) as error:
            self.latest = None
            if not self.check_failed:
                logging.warning('AIO release check failed: %s', error)
            self.check_failed = True
            return 300
        if self.check_failed:
            logging.info('AIO release check recovered')
            self.check_failed = False
        return 3600

    def page_for_day(self, day, dismissals=()):
        latest = self.latest
        if latest and f'{day}_{latest}' not in dismissals:
            url = f'https://github.com/{self.repository}/releases/tag/{latest}'
            return self.page.substitute(current=self.current, latest=latest, release_url=url, day=day).encode()


class NoticeHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def do_GET(self):
        if self.path != '/':
            self.send_error(404)
            return
        day = datetime.now(timezone.utc).date().isoformat()
        dismissals = self.headers.get('X-AIO-Dismissed-Notices', '').split()
        page = self.server.notice.page_for_day(day, dismissals)
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
    logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
    os.umask(0o077)
    socket = Path('/run/envoy/update-notice.sock')
    socket.unlink(missing_ok=True)
    with UnixStreamServer(str(socket), NoticeHandler) as server:
        page = Template(Path('/etc/envoy/pages/update-notice.html').read_text())
        server.notice = Notice(page, repository, current)
        Thread(target=server.notice.refresh, daemon=True).start()
        server.serve_forever()


if __name__ == '__main__':
    main()

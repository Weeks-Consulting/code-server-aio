"""Check release selection and notice delivery without GitHub or Docker."""

from contextlib import closing
import io
import json
import sqlite3
from string import Template
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from test_start import ROOT, load


notice = load('update-notice')
PAGE = Template((ROOT / 'build/runtime/envoy/pages/update-notice.html').read_text())


class UpdateNoticeTests(unittest.TestCase):
    def test_selects_only_newer_stable_releases_with_deployment_assets(self):
        release = {'tag_name': 'v4.146.0-10',
                   'assets': [{'name': 'SHA256SUMS'}, {'name': 'code-server-aio.tar.gz'}]}
        current = notice.release_version('v4.146.0-9')
        cases = (
            ({}, 'v4.146.0-10'),
            ({'tag_name': 'v4.146.0-9'}, None),
            ({'tag_name': 'v4.146.0'}, None),
            ({'draft': True}, None),
            ({'prerelease': True}, None),
            ({'assets': []}, None),
        )
        for changes, expected in cases:
            with self.subTest(changes=changes), patch.object(
                    notice.urllib.request, 'urlopen',
                    return_value=io.BytesIO(json.dumps({**release, **changes}).encode())) as fetch:
                self.assertEqual(notice.newer_release('example/aio', current), expected)
                self.assertEqual(fetch.call_args.args[0].full_url,
                                 'https://api.github.com/repos/example/aio/releases/latest')

    def test_notice_once_per_identity_per_utc_day(self):
        with closing(sqlite3.connect(':memory:')) as database:
            state = notice.Notice(database, PAGE, 'example/aio', 'v4.146.0-1')
            self.assertIsNone(state.for_subject(b'alice', '2026-11-23'))
            state.latest = 'v4.146.0-2'
            page = state.for_subject(b'alice', '2026-11-23')
            self.assertIn(b'https://github.com/example/aio/releases/tag/v4.146.0-2', page)
            self.assertIn(b'v4.146.0-1', page)
            self.assertIsNone(state.for_subject(b'alice', '2026-11-23'))
            self.assertIsNotNone(state.for_subject(b'bob', '2026-11-23'))
            restarted = notice.Notice(database, PAGE, 'example/aio', 'v4.146.0-1')
            restarted.latest = state.latest
            self.assertIsNone(restarted.for_subject(b'alice', '2026-11-23'))
            self.assertIsNotNone(restarted.for_subject(b'alice', '2026-11-24'))

    def test_handler_serves_page_and_then_allows_continuation(self):
        request = b'POST / HTTP/1.1\r\nHost: notices\r\nContent-Length: 5\r\n\r\nalice'
        with closing(sqlite3.connect(':memory:')) as database:
            state = notice.Notice(database, PAGE, 'example/aio', 'v4.146.0-1')
            state.latest = 'v4.146.0-2'
            for status in (b'200', b'204'):
                response = io.BytesIO()
                connection = SimpleNamespace(makefile=lambda *args: io.BytesIO(request), sendall=response.write)
                notice.NoticeHandler(connection, '', SimpleNamespace(notice=state))
                headers, body = response.getvalue().split(b'\r\n\r\n', 1)
                self.assertEqual(headers.split()[1], status)
                if status == b'200':
                    self.assertIn(b'Continue to code-server', body)
                else:
                    self.assertEqual(body, b'')

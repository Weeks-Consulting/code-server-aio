"""Check release selection and notice delivery without GitHub or Docker."""

from datetime import datetime, timezone
import io
import json
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

    def test_notice_form_carries_the_day_and_release_shown(self):
        state = notice.Notice(PAGE, 'example/aio', 'v4.146.0-1')
        self.assertIsNone(state.page_for_day('2026-11-23'))
        state.latest = 'v4.146.0-2'
        page = state.page_for_day('2026-11-23')
        self.assertIn(b'https://github.com/example/aio/releases/tag/v4.146.0-2', page)
        self.assertIn(b'v4.146.0-1', page)
        self.assertIn(b'value="2026-11-23_v4.146.0-2"', page)
        self.assertEqual(state.page_for_day('2026-11-23'), page)
        dismissed = ['2026-11-23_v4.146.0-2']
        self.assertIsNone(state.page_for_day('2026-11-23', dismissed))
        self.assertIsNotNone(state.page_for_day('2026-11-24', dismissed))
        state.latest = 'v4.146.0-3'
        self.assertIsNotNone(state.page_for_day('2026-11-23', dismissed))

    def test_handler_serves_cached_notice_and_no_content_without_an_update(self):
        state = notice.Notice(PAGE, 'example/aio', 'v4.146.0-1')
        today = datetime.now(timezone.utc).date().isoformat()
        for latest, dismissed, status in (('v4.146.0-2', '', b'200'),
                                          ('v4.146.0-2', f'{today}_v4.146.0-2', b'204'),
                                          (None, '', b'204')):
            request = f'GET / HTTP/1.1\r\nHost: notices\r\nX-AIO-Dismissed-Notices: {dismissed}\r\n\r\n'.encode()
            state.latest = latest
            response = io.BytesIO()
            connection = SimpleNamespace(makefile=lambda *args: io.BytesIO(request), sendall=response.write)
            notice.NoticeHandler(connection, '', SimpleNamespace(notice=state))
            headers, body = response.getvalue().split(b'\r\n\r\n', 1)
            self.assertEqual(headers.split()[1], status)
            if status == b'200':
                self.assertIn(b'Continue to code-server', body)
            else:
                self.assertEqual(body, b'')

    def test_logs_failure_and_recovery_once_per_outage(self):
        state = notice.Notice(PAGE, 'example/aio', 'v4.146.0-1')
        state.latest = 'v4.146.0-2'
        with patch.object(notice, 'newer_release', side_effect=[
            OSError('offline'), OSError('still offline'), 'v4.146.0-3', None
        ]), self.assertLogs(level='INFO') as logs:
            self.assertEqual(state.check_for_update(), 300)
            self.assertIsNone(state.latest)
            self.assertEqual(state.check_for_update(), 300)
            self.assertEqual(state.check_for_update(), 3600)
            self.assertEqual(state.latest, 'v4.146.0-3')
            self.assertEqual(state.check_for_update(), 3600)
        self.assertEqual(logs.output, [
            'WARNING:root:AIO release check failed: offline',
            'INFO:root:AIO release check recovered',
        ])

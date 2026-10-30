"""Guard publication ordering and exercise host-specific CI bootstrap commands."""
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class PipelineTests(unittest.TestCase):
    def test_release_assets_wait_for_publication_and_all_checks(self):
        workflow = (ROOT / '.github/workflows/container.yaml').read_text()
        jobs = dict(re.findall(r'^  ([\w-]+):\n(.*?)(?=^  [\w-]+:|\Z)',
                               workflow.split('jobs:\n', 1)[1], re.M | re.S))

        def dependencies(job):
            value = re.search(r'^    needs: (.+)$', jobs[job], re.M)[1]
            return {item.strip() for item in value.strip('[]').split(',')}

        self.assertIn('publish', dependencies('release-assets'))
        self.assertTrue({'test', 'check-host'} <= dependencies('publish'))
        self.assertNotRegex(jobs['release-assets'], r'always\(\)|continue-on-error: true')
        self.assertIn("if: github.event_name == 'release'", jobs['release-assets'])
        # Only the source job may dispatch rebuilds; test jobs never get write access.
        self.assertIn('      actions: write\n', jobs['source'])
        self.assertRegex(jobs['test'], r'permissions:\n      contents: read\n    timeout')
        self.assertNotIn('packages: write', jobs['test'])
        for job in ('test', 'check-host'):
            self.assertIn("if: needs.source.outputs.dispatched != 'true'", jobs[job])
        host = jobs['check-host']
        self.assertIn('runner: macos-14', host)
        self.assertIn('runner: ubuntu-24.04', host)
        self.assertRegex(host, r'container-image: debian:13-slim@sha256:[a-f0-9]{64}')
        self.assertEqual(host.count('run: sh build/tests/check.sh'), 1)
        self.assertEqual(host.count('run: sh build/tests/install-host-deps.sh'), 1)

    def test_host_bootstrap_only_separates_platform_setup(self):
        for system in ('Linux', 'Darwin', 'unsupported'):
            with self.subTest(system=system), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                tools = root / 'bin'
                tools.mkdir()
                log = root / 'calls'
                environment = root / 'github-env'
                environment.touch()
                for command, body in {
                    'uname': 'echo "$TEST_SYSTEM"',
                    'apt-get': 'echo "apt-get $*" >> "$TEST_LOG"',
                    'brew': 'echo "brew $*" >> "$TEST_LOG"; if [ "$1" = --prefix ]; then echo /test/brew; fi',
                }.items():
                    path = tools / command
                    path.write_text('#!/bin/sh\n' + body + '\n')
                    path.chmod(0o755)
                result = subprocess.run(['sh', str(ROOT / 'build/tests/install-host-deps.sh')],
                                        env={**os.environ, 'PATH': f'{tools}:{os.environ["PATH"]}',
                                             'TEST_SYSTEM': system, 'TEST_LOG': str(log),
                                             'RUNNER_TEMP': str(root), 'GITHUB_ENV': str(environment)},
                                        capture_output=True, text=True)
                if system == 'unsupported':
                    self.assertNotEqual(result.returncode, 0)
                    self.assertFalse(log.exists())
                    continue
                self.assertEqual(result.returncode, 0, result.stderr)
                calls = log.read_text()
                # End users and host checks no longer need the GitHub CLI.
                self.assertNotIn('gh', calls.split())
                if system == 'Linux':
                    self.assertIn('apt-get update\n', calls)
                    self.assertIn('docker-cli docker-compose', calls)
                    self.assertNotIn('brew', calls)
                    self.assertEqual(environment.read_text(), '')
                else:
                    self.assertIn('brew install python shellcheck docker docker-compose', calls)
                    self.assertNotIn('apt-get', calls)
                    plugin = root / 'docker-config/cli-plugins/docker-compose'
                    self.assertEqual(os.readlink(plugin), '/test/brew/bin/docker-compose')
                    self.assertEqual(environment.read_text(), f'DOCKER_CONFIG={root}/docker-config\n')

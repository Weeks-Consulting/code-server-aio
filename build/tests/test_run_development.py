"""Check development project selection and cleanup without starting Docker containers."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / 'tools/run-development.sh'


class DevelopmentRunTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        tools = self.root / 'build/tools'
        tools.mkdir(parents=True)
        self.script = tools / SCRIPT.name
        shutil.copyfile(SCRIPT, self.script)
        (tools / 'dependencies.py').touch()
        self.log = self.root / 'docker.jsonl'
        docker = self.root / 'docker'
        docker.write_text(
            '#!/usr/bin/env python3\n'
            'import json, os, sys\n'
            'args = sys.argv[1:]\n'
            "with open('docker.jsonl', 'a') as log:\n"
            "    log.write(json.dumps(args) + '\\n')\n"
            "if args[1] == 'ls' and args[0] == os.environ.get('OCCUPIED_RESOURCE'):\n"
            "    print('existing-resource')\n"
            "if args[-2:] == ['up', '--build']:\n"
            "    sys.exit(int(os.environ.get('UP_EXIT', '0')))\n"
        )
        docker.chmod(0o755)

    def run_development(self, **overrides):
        env = {**os.environ, 'PATH': f"{self.root}{os.pathsep}{os.environ['PATH']}",
               'COMPOSE_PROJECT_NAME': 'existing-production', **overrides}
        result = subprocess.run(['sh', str(self.script)], cwd=self.root, env=env, capture_output=True, text=True)
        self.assertFalse((self.root / '.run-development.lock').exists())
        return result

    def test_failed_up_only_cleans_up_its_development_project(self):
        result = self.run_development(UP_EXIT='19')
        self.assertEqual(result.returncode, 19, result.stderr)
        containers, networks, config, up, down = self.read_calls()
        project = up[2]
        self.assertRegex(project, r'^code-server-aio-dev-[0-9a-f]{24}$')
        project_filter = f'label=com.docker.compose.project={project}'
        self.assertEqual(containers, ['container', 'ls', '--all', '--quiet', '--filter', project_filter])
        self.assertEqual(networks, ['network', 'ls', '--quiet', '--filter', project_filter])
        compose = ['compose', '--project-name', project, '--env-file', '.env',
                   '-f', 'compose.yaml', '-f', 'compose.build.yaml', '-f', 'compose.dev.yaml']
        self.assertEqual(config, compose + ['config', '--quiet'])
        self.assertEqual(up, compose + ['up', '--build'])
        self.assertEqual(down, compose + ['down'])
        self.log.unlink()
        result = self.run_development()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.read_calls()[-1], down)

    def test_existing_project_resources_prevent_start_and_cleanup(self):
        for resource in ('container', 'network'):
            with self.subTest(resource=resource):
                result = self.run_development(OCCUPIED_RESOURCE=resource)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('already has containers or networks', result.stderr)
                self.assertEqual([call[:2] for call in self.read_calls()], [['container', 'ls'], ['network', 'ls']])
                self.log.unlink()

    def read_calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()]

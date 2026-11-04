"""Exercise release validation and weekly source selection from the CI helper."""

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
VERSION = json.loads((ROOT / "build/versions.json").read_text())["packages"]["code-server"]["version"]
# Stub GitHub and Docker: log each call and answer `gh api` with $LATEST_TAG.
STUB = '#!/bin/sh\necho "$(basename "$0") $*" >> "$TEST_LOG"\n'
GH_STUB = STUB + r'''
case "$1 $2" in
  "api "*) printf '%s\n' "$LATEST_TAG";;
  "release view") [ "${RELEASE_EXISTS:-true}" = true ] || exit 1; printf '%s\n' "${RELEASE_READY:-true}";;
  "release edit") exit "${EDIT_STATUS:-0}";;
esac
'''
DOCKER_STUB = STUB + r'''
case "$*" in
  *imagetools\ inspect*) printf '%s\n' "${IMAGE_DIGEST:-sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa}";;
  *imagetools\ create*) exit "${CREATE_STATUS:-0}";;
esac
'''


class ReleaseTests(unittest.TestCase):
    def run_script(self, command, tag, **env):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tools = root / "bin"
            tools.mkdir()
            for name, body in (("gh", GH_STUB), ("docker", DOCKER_STUB)):
                (tools / name).write_text(body)
                (tools / name).chmod(0o755)
            output, log = root / "output", root / "calls"
            result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True,
                                    env={**os.environ, "PATH": f"{tools}:{os.environ['PATH']}",
                                         "RELEASE_TAG": tag, "GITHUB_OUTPUT": str(output),
                                         "GITHUB_STEP_SUMMARY": str(root / "summary"),
                                         "TEST_LOG": str(log), "PUBLISH_LATEST": "false", **env})
            read = lambda path: path.read_text() if path.exists() else ""
            return result, read(output), read(log)

    def select(self, event, tag="", **env):
        return self.run_script(["bash", "build/ci/release.sh", "select-source"], tag, EVENT_NAME=event,
                               GH_REPO="owner/repo",
                               WORKFLOW_REF="owner/repo/.github/workflows/container.yaml@refs/heads/main",
                               **env)

    def test_release_validation(self):
        for suffix in ("", "-1", "-2", "-12"):
            with self.subTest(suffix=suffix):
                result, output, _ = self.run_script(["bash", "build/ci/release.sh", "release-version"], f"v{VERSION}{suffix}")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(output, f"version={VERSION}\n")
        result, _, _ = self.run_script(["bash", "build/ci/release.sh", "release-version"], "")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_invalid_releases(self):
        for tag in (f"v{VERSION}-rc.1", f"v{VERSION}-rc.12", f"v{VERSION}-0", f"v{VERSION}-01", f"v{VERSION}-rc.0",
                    f"v{VERSION}-rc.01", f"v{VERSION}-fix", f"v{VERSION}-1-rc.1",
                    f"v{VERSION}+1", VERSION, "v0.0.0-2"):
            with self.subTest(tag=tag):
                result, _, _ = self.run_script(["bash", "build/ci/release.sh", "release-version"], tag)
                self.assertNotEqual(result.returncode, 0)

    def test_publication_modes(self):
        tag = f"v{VERSION}-2"
        for event, requested, source_ref, publish, dispatched in (
            ("pull_request", "false", "refs/pull/1/merge", "false", False),
            ("push", "false", "refs/heads/main", "false", False),
            ("workflow_dispatch", "false", "refs/heads/main", "false", False),
            ("workflow_dispatch", "true", "refs/heads/main", "false", True),
            ("workflow_dispatch", "true", f"refs/tags/{tag}", "true", False),
            ("workflow_dispatch", "true", f"refs/tags/v{VERSION}-1", "false", True),
            ("schedule", "false", "refs/heads/main", "false", True),
            ("push", "false", f"refs/tags/{tag}", "true", False),
        ):
            with self.subTest(event=event, requested=requested, source_ref=source_ref):
                result, output, calls = self.select(event, tag if source_ref.startswith("refs/tags/") else "",
                                                    PUBLISH_LATEST=requested, SOURCE_REF=source_ref,
                                                    LATEST_TAG=tag)
                self.assertEqual(result.returncode, 0, result.stderr)
                values = dict(line.split("=", 1) for line in output.splitlines())
                self.assertEqual(values["publish"], publish)
                self.assertEqual(values["tag"], tag if publish == "true" else "")
                self.assertEqual(values["dispatched"], "true" if dispatched else "false")
                dispatch = f"gh workflow run container.yaml --ref {tag} -f publish_latest=true\n"
                self.assertEqual(dispatch in calls, dispatched)

    def test_weekly_rebuild_source(self):
        for suffix in ("", "-1", "-2", "-12", "-rc.1", "-0", "-01", "-fix"):
            tag = f"v{VERSION}{suffix}"
            with self.subTest(tag=tag):
                result, output, calls = self.select("schedule", SOURCE_REF="refs/heads/main", LATEST_TAG=tag)
                if suffix in ("", "-1", "-2", "-12"):
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(output, "tag=\npublish=false\ndispatched=true\n")
                    self.assertIn(f"workflow run container.yaml --ref {tag} ", calls)
                else:
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(output, "")
                    self.assertNotIn("workflow run", calls)

    def test_only_current_stable_release_moves_latest(self):
        for latest, prerelease, moves_latest in (
                ('v2.0.0', 'false', True), ('v3.0.0', 'false', False),
                ('v2.0.0', 'true', False)):
            with self.subTest(latest=latest, prerelease=prerelease):
                result, _, calls = self.run_script(["bash", "build/ci/release.sh", "publish-tags"], 'v2.0.0',
                                                   LATEST_TAG=latest, PRERELEASE=prerelease,
                                                   IMAGE='ghcr.io/example/app',
                                                   DIGEST='sha256:' + 'a' * 64, GH_REPO='example/app')
                self.assertEqual(result.returncode, 0, result.stderr)
                docker = next(line for line in calls.splitlines() if line.startswith('docker '))
                self.assertIn('--tag ghcr.io/example/app:2.0.0', docker)
                self.assertEqual('--tag ghcr.io/example/app:latest' in docker, moves_latest)
                self.assertTrue(docker.endswith('ghcr.io/example/app@sha256:' + 'a' * 64))

    def test_publish_release_creation_and_retry(self):
        for exists in ('true', 'false'):
            with self.subTest(exists=exists):
                result, _, calls = self.run_script(
                    ['bash', 'build/ci/release.sh', 'publish-release'], 'v2.0.0', RELEASE_EXISTS=exists)
                self.assertEqual(result.returncode, 0, result.stderr)
                if exists == 'false':
                    self.assertIn('gh release create v2.0.0 ', calls)
                    self.assertIn('--verify-tag', calls)
                    self.assertIn('--prerelease --latest=false', calls)
                else:
                    self.assertIn('gh release upload v2.0.0 ', calls)
                    self.assertNotIn('release edit', calls)
                self.assertIn('code-server-aio.tar.gz SHA256SUMS', calls)

    def test_promote_existing_digest_without_building(self):
        result, _, calls = self.run_script(
            ['bash', 'build/ci/release.sh', 'promote-release'], 'v2.0.0-1', IMAGE='ghcr.io/example/app')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('inspect ghcr.io/example/app:2.0.0-1', calls)
        self.assertIn('create --tag ghcr.io/example/app:latest ghcr.io/example/app@sha256:' + 'a' * 64, calls)
        self.assertIn('gh release edit v2.0.0-1 --prerelease=false --latest', calls)
        self.assertLess(calls.index('create --tag'), calls.index('release edit'))
        self.assertNotIn('buildx build', calls)

    def test_promotion_fails_closed(self):
        for env in ({'RELEASE_EXISTS': 'false'}, {'RELEASE_READY': 'false'},
                    {'IMAGE_DIGEST': 'invalid'}, {'CREATE_STATUS': '1'}):
            with self.subTest(env=env):
                result, _, calls = self.run_script(
                    ['bash', 'build/ci/release.sh', 'promote-release'], 'v2.0.0',
                    IMAGE='ghcr.io/example/app', **env)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn('release edit', calls)
                if 'CREATE_STATUS' not in env:
                    self.assertNotIn('create --tag', calls)
        result, _, calls = self.run_script(
            ['bash', 'build/ci/release.sh', 'promote-release'], 'v2.0.0;echo injected')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(calls, '')


if __name__ == "__main__":
    unittest.main()

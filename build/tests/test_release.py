"""Exercise release validation and weekly source selection from the workflow."""

import os
from pathlib import Path
import subprocess
import tempfile
import textwrap
import unittest


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = (ROOT / ".github/workflows/container.yaml").read_text()
VERSION = (ROOT / "build/code-server.version").read_text().strip()
VALIDATION = textwrap.dedent(WORKFLOW.split("python3 - <<'PY'\n", 1)[1].split("          PY\n", 1)[0])
SOURCE = textwrap.dedent(WORKFLOW.split('        run: |\n', 1)[1].split('\n  test:', 1)[0])


class ReleaseTests(unittest.TestCase):
    def run_script(self, command, tag, **env):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output"
            result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True,
                                    env={**os.environ, "RELEASE_TAG": tag,
                                         "GITHUB_OUTPUT": str(output), "PUBLISH_LATEST": "false", **env})
            return result, output.read_text() if output.exists() else ""

    def test_release_validation(self):
        for suffix in ("", "-1", "-2", "-12"):
            with self.subTest(suffix=suffix):
                result, output = self.run_script(["python3", "-c", VALIDATION], f"v{VERSION}{suffix}")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(output, f"version={VERSION}\n")
        result, _ = self.run_script(["python3", "-c", VALIDATION], "")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_invalid_releases(self):
        for tag in (f"v{VERSION}-rc.1", f"v{VERSION}-rc.12", f"v{VERSION}-0", f"v{VERSION}-01", f"v{VERSION}-rc.0",
                    f"v{VERSION}-rc.01", f"v{VERSION}-fix", f"v{VERSION}-1-rc.1",
                    f"v{VERSION}+1", VERSION, "v0.0.0-2"):
            with self.subTest(tag=tag):
                result, _ = self.run_script(["python3", "-c", VALIDATION], tag)
                self.assertNotEqual(result.returncode, 0)

    def test_publication_modes(self):
        command = ["bash", "-euo", "pipefail", "-c",
                   'gh() { printf "%s\\n" "$LATEST_TAG"; }\n' + SOURCE]
        for event, requested, publish, rebuild in (
            ("pull_request", "false", "false", "false"),
            ("push", "false", "false", "false"),
            ("workflow_dispatch", "false", "false", "false"),
            ("workflow_dispatch", "true", "true", "true"),
            ("schedule", "false", "true", "true"),
            ("release", "false", "true", "false"),
        ):
            with self.subTest(event=event, requested=requested):
                tag = f"v{VERSION}-2"
                result, output = self.run_script(command, tag if event == "release" else "",
                                                EVENT_NAME=event, PUBLISH_LATEST=requested,
                                                SOURCE_SHA="original-sha", GH_REPO="owner/repo", LATEST_TAG=tag)
                self.assertEqual(result.returncode, 0, result.stderr)
                values = dict(line.split("=", 1) for line in output.splitlines())
                self.assertEqual(values["publish"], publish)
                self.assertEqual(values["rebuild"], rebuild)
                self.assertEqual(values["ref"], f"refs/tags/{tag}" if rebuild == "true" else "original-sha")
                self.assertEqual(values["tag"], tag if publish == "true" else "")

    def test_weekly_rebuild_source(self):
        # Stub GitHub's response while running the workflow's actual shell block.
        command = ["bash", "-euo", "pipefail", "-c",
                   'gh() { printf "%s\\n" "$LATEST_TAG"; }\n' + SOURCE]
        for suffix in ("", "-1", "-2", "-12", "-rc.1", "-0", "-01", "-fix"):
            tag = f"v{VERSION}{suffix}"
            with self.subTest(tag=tag):
                result, output = self.run_script(command, "", EVENT_NAME="schedule",
                                                SOURCE_SHA="original-sha", GH_REPO="owner/repo",
                                                LATEST_TAG=tag)
                if suffix in ("", "-1", "-2", "-12"):
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(output, f"ref=refs/tags/{tag}\ntag={tag}\npublish=true\nrebuild=true\n")
                else:
                    self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()

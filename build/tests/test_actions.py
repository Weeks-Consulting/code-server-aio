"""Check the local workflow wrapper without downloads or Docker containers."""

import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class ActionsTests(unittest.TestCase):
    def test_native_runner_and_dependency_mappings(self):
        for system, machine, arch, asset in (
            ("Darwin", "arm64", "arm64", "arm64"),
            ("Darwin", "x86_64", "amd64", "x86_64"),
            ("Linux", "aarch64", "arm64", "arm64"),
            ("Linux", "x86_64", "amd64", "x86_64"),
        ):
            with self.subTest(system=system, machine=machine), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                project = root / "checkout with spaces"
                tests = project / "build/tests"
                tests.mkdir(parents=True)
                script = tests / "actions.sh"
                script.write_bytes((ROOT / "build/tests/actions.sh").read_bytes())
                workflows = project / ".github/workflows"
                workflows.mkdir(parents=True)
                (workflows / "container.yaml").write_bytes((ROOT / ".github/workflows/container.yaml").read_bytes())
                pins = json.loads((ROOT / "build/build-versions.json").read_text())
                log = root / "arguments.json"
                executable = (f"#!{sys.executable}\nimport json, os, pathlib, sys\n"
                              "workflow = pathlib.Path(sys.argv[sys.argv.index('--workflows') + 1]).read_text()\n"
                              f"pathlib.Path({str(log)!r}).write_text(json.dumps(dict(args=sys.argv[1:], host=os.environ.get('DOCKER_HOST'), workflow=workflow)))\n").encode()
                archive = root / "act.tar.gz"
                with tarfile.open(archive, "w:gz") as bundle:
                    info = tarfile.TarInfo("act")
                    info.mode, info.size = 0o755, len(executable)
                    bundle.addfile(info, io.BytesIO(executable))
                digest = hashlib.sha256(archive.read_bytes()).hexdigest()
                pins["packages"]["act"]["sha256"] = {f"act_{system}_{asset}.tar.gz": digest}
                (project / "build/build-versions.json").write_text(json.dumps(pins))
                tools = root / "bin"
                tools.mkdir()
                (tools / "uname").write_text(f'#!/bin/sh\ncase "$1" in -s) echo {system};; -m) echo {machine};; esac\n')
                docker_log = root / "docker-calls.jsonl"
                (tools / "docker").write_text(
                    f"#!{sys.executable}\nimport json, pathlib, sys\n"
                    f"with pathlib.Path({str(docker_log)!r}).open('a') as log: log.write(json.dumps(sys.argv[1:]) + '\\n')\n"
                    "if sys.argv[1:3] == ['context', 'inspect']: print('unix:///example/docker.sock')\n"
                    "elif sys.argv[1:3] == ['volume', 'inspect']: print('/var/lib/docker/volumes/test-volume/_data')\n"
                    "elif sys.argv[1:3] not in (['volume', 'create'], ['volume', 'rm']): sys.exit(1)\n")
                (tools / "curl").write_text(
                    f"#!{sys.executable}\nimport shutil, sys\n"
                    f"shutil.copyfile({str(archive)!r}, sys.argv[sys.argv.index('--output') + 1])\n")
                for tool in tools.iterdir():
                    tool.chmod(0o755)
                env = {**os.environ, "PATH": f"{tools}:{os.environ['PATH']}"}
                env.pop("DOCKER_HOST", None)
                env.pop("DOCKER_CONTEXT", None)
                result = subprocess.run(["sh", str(script), "--list"], capture_output=True, text=True, env=env)
                self.assertEqual(result.returncode, 0, result.stderr)
                recorded = json.loads(log.read_text())
                args = recorded["args"]
                platforms = [args[i + 1] for i, arg in enumerate(args) if arg == "-P"]
                self.assertRegex(platforms[0], r"^ubuntu-24.04=catthehacker/ubuntu:act-24.04@sha256:[a-f0-9]{64}$")
                self.assertEqual(platforms[1], "ubuntu-24.04-arm=" + platforms[0].split("=", 1)[1])
                self.assertEqual(args[args.index("--matrix") + 1], f"arch:{arch}")
                self.assertEqual(args[args.index("--container-architecture") + 1], f"linux/{arch}")
                self.assertIn("TEST_HOME_STORAGE=" + ("volume" if system == "Darwin" else "bind"), args)
                if system == "Darwin":
                    self.assertEqual(args[args.index("--container-daemon-socket") + 1], "/var/run/docker.sock")
                    self.assertEqual(recorded["host"], "unix:///example/docker.sock")
                else:
                    self.assertNotIn("--container-daemon-socket", args)
                self.assertEqual(args[-1], "--list")
                # act's schema lacks concurrency `queue`; only that line may be dropped.
                original = (ROOT / ".github/workflows/container.yaml").read_text().splitlines(keepends=True)
                self.assertEqual(recorded["workflow"],
                                 "".join(line for line in original if line.strip() != "queue: max"))
                self.assertNotEqual(recorded["workflow"], "".join(original))
                self.assertIn("--rm", args)
                self.assertEqual(args[args.index("--env-file") + 1], "/dev/null")
                self.assertFalse(list(project.glob(".test-tmp.*")))
                calls = docker_log.read_text() if docker_log.exists() else ""
                self.assertNotIn('"volume"', calls)
                if system == "Darwin":
                    env["DOCKER_HOST"] = "unix:///custom/docker.sock"
                    result = subprocess.run(["sh", str(script)], capture_output=True, text=True, env=env)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    recorded = json.loads(log.read_text())
                    self.assertEqual(recorded["host"], "unix:///custom/docker.sock")
                    self.assertIn("TEST_TMPDIR=/var/lib/docker/volumes/test-volume/_data", recorded["args"])
                    calls = [json.loads(line) for line in docker_log.read_text().splitlines()]
                    created = next(call[2] for call in calls if call[:2] == ["volume", "create"])
                    self.assertIn(["volume", "rm", created], calls)
                    self.assertFalse(list(project.glob(".test-tmp.*")))


if __name__ == "__main__":
    unittest.main()

"""Exercise the proxy startup gate without starting workspace services."""

from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class StartupTests(unittest.TestCase):
    def test_proxy_gate_and_failure_shutdown(self):
        script = (ROOT / "build/runtime/start-code-server.sh").read_text().split("# Bind mounts", 1)[0]
        # Shorten only the deadline; run the actual loop and EXIT trap.
        script = script.replace("SECONDS + 120", "SECONDS + 1")
        for status, expected in (("503", 0), ("403", 0), ("200", 1), ("000", 1)):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as directory:
                marker = Path(directory) / "shutdown"
                candidate = script.replace("/usr/bin/curl", "fake_curl").replace("/usr/bin/supervisorctl", "fake_supervisorctl")
                functions = f"fake_curl() {{ echo {status}; }}\nfake_supervisorctl() {{ touch '{marker}'; }}\n"
                result = subprocess.run(["bash", "-c", functions + candidate], capture_output=True, text=True, timeout=5)
                self.assertEqual(result.returncode, expected, result.stderr)
                self.assertEqual(marker.exists(), expected != 0)
                if expected:
                    self.assertIn("Access proxy did not become ready", result.stderr)


if __name__ == "__main__":
    unittest.main()

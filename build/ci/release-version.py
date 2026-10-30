#!/usr/bin/env python3
"""Validate the code-server version and optional release tag; output the version."""
import json
import os
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]

version = json.loads((ROOT / "build/versions.json").read_text())["packages"]["code-server"]["version"]
if not re.fullmatch(r"\d+\.\d+\.\d+", version):
    raise SystemExit("build/versions.json must contain a version such as 4.141.0")
expected = "v" + version
tag = os.environ.get("RELEASE_TAG", "")
if tag and not re.fullmatch(re.escape(expected) + r"(?:-[1-9][0-9]*)?", tag):
    raise SystemExit(f"Release tag must be {expected} or {expected}-N (N >= 1) to match build/versions.json (got {tag})")
print(f"Building code-server {version}" + (f" for release {tag}" if tag else ""))
with open(os.environ["GITHUB_OUTPUT"], "a") as output:
    output.write(f"version={version}\n")

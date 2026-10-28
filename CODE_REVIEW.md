# Code Review

Follow-up review, 2026-10-28. All ten findings from the first review are fixed in the
working tree, so they have been removed. All three follow-up findings below are now
resolved as well; their descriptions are retained for context. No open findings remain.

Follow-up fix verification: `sh build/tests/check.sh` passes (103 tests, two root-only
tests skipped, plus pin, syntax, shellcheck, and Compose checks). Targeted tests cover
bootstrap failure messages, matching embedded downloaders, CI invocation, temporary
file cleanup, and restoring `PATH` after success or failure. The new download probe
also passed against the real pinned Linux binary. Native macOS execution remains for
the macOS CI job; it was not run on this Linux host. Runtime integration suites were
not rerun because these changes affect documentation, bootstrap diagnostics, and CI.

Earlier fix verification confirmed all four pinned GitHub CLI v2.103.3 checksums
against upstream release files, Linux attestation capability, and the friendly
empty-domain startup error in the test container.

| # | Severity | Area | Finding | Status |
| --- | --- | --- | --- | --- |
| 1 | Low | Docs | Development guide doesn't list the GitHub CLI pin or the command that updates it | Resolved |
| 2 | Low | Bootstrap | A failed GitHub CLI download ends in a Python traceback | Resolved |
| 3 | Low | CI coverage | CI never runs the macOS GitHub CLI download path | Resolved |

---

## 1. Low: Development guide doesn't list the GitHub CLI pin or the command that updates it

**Where:** [docs/development.md:115](docs/development.md#L115),
[build/build-versions.json](build/build-versions.json)

The `gh` pin used by the installers is stored in `build/build-versions.json`. Because of
that, `just build-upgrade` updates it and `just upgrade` does not. The development guide
still says this file holds only act, GitHub Actions, the act runner image, and RustFS.
A maintainer refreshing shipped dependencies with `just upgrade` would not know that
the installer's `gh` version is updated separately.

**Resolved:** The development guide now lists the installer's GitHub CLI pin and
explains that `just build-upgrade` updates its checksums and the embedded pins in
`manage.py` and `build/tools/install.sh`.

## 2. Low: A failed GitHub CLI download ends in a Python traceback

**Where:** [build/tools/install.sh](build/tools/install.sh) (embedded `GH_PY` block),
[manage.py](manage.py) (`ensure_gh`)

The bootstrap calls `ensure_gh()` without catching errors. A network failure, an
unsupported platform, or a checksum mismatch prints a raw Python traceback. The install
still stops before anything is run, so this is only a usability problem. `manage.py`
handles the same errors cleanly through its top-level `OSError`/`ValueError` handler.

**Resolved:** The embedded bootstrap catches `OSError` and `ValueError` around
`ensure_gh(...)`, exiting with a concise explanation. The shared downloader function
is unchanged. Regression coverage includes network, platform, and checksum errors.

## 3. Low: CI never runs the macOS GitHub CLI download path

**Where:** [build/tests/install-host-deps.sh](build/tests/install-host-deps.sh)

The macOS host check installs `gh` with Homebrew. `ensure_gh()` therefore finds a
capable CLI and never downloads or extracts the pinned `macOS_*.zip` asset. Only a unit
test with a synthetic zip covers that path. If an upstream release changes the macOS
archive layout (currently `gh_<version>_macOS_<arch>/bin/gh`), CI wouldn't notice until
a user ran the installer on a Mac without `gh`.

**Resolved:** After installing Homebrew dependencies, macOS host CI runs
`build/tests/check-gh-download.py`. This probe clears `PATH` while calling
`ensure_gh()` into a temporary directory, restores `PATH`, and executes the downloaded
binary's `attestation verify --help` command. It removes the temporary directory on
success or failure and keeps Homebrew's CLI available for the rest of the job.

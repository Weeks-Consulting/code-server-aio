#!/usr/bin/env python3
"""Initialize a deployment backup passphrase without executing its .env file."""

import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import secrets
import signal
import stat
import subprocess
import sys
import tempfile


# Kept in sync with manage.py: both helpers ship as standalone release files.
def validate_dotenv(text):
    """Require quoted dotenv values to end on the same physical line."""
    assignment = re.compile(r"^[ \t]*(?:export[ \t]+)?[A-Za-z_][A-Za-z0-9_]*[ \t]*[=:][ \t]*(.*)$")
    for number, line in enumerate(text.splitlines(), 1):
        match = assignment.match(line)
        if not match or not match[1].startswith(("'", '"')):
            continue
        value = match[1]
        escaped = False
        for character in value[1:]:
            if escaped:
                escaped = False
            elif character == "\\":
                # Compose's dotenv parser also treats backslash as an escape in single quotes.
                escaped = True
            elif character == value[0]:
                break
        else:
            raise ValueError(f"Multiline or unterminated quoted .env value at line {number}; use a single-line value.")


def resolve_key(path):
    """Resolve the deployment file's key without a daemon or a shell override."""
    env = dict(os.environ)
    env.pop("BACKUP_GPG_PASSPHRASE", None)
    try:
        result = subprocess.run(
            ["docker", "compose", "--project-directory", str(path.parent),
             "--env-file", str(path), "-f", "-", "config", "--format", "json"],
            input="services:\n  backup-key:\n    image: unused\n    environment:\n"
                  "      BACKUP_GPG_PASSPHRASE: ${BACKUP_GPG_PASSPHRASE:-}\n",
            env=env, capture_output=True, text=True, check=True,
        )
        return json.loads(result.stdout)["services"]["backup-key"]["environment"]["BACKUP_GPG_PASSPHRASE"]
    except (OSError, subprocess.CalledProcessError, ValueError, KeyError):
        sys.exit("Could not resolve the backup key. Check Docker Compose and the .env syntax.")


def unwind_on_termination():
    """Make SIGHUP and SIGTERM unwind like Ctrl-C so the temporary .env copy (which holds the key) is removed.

    Signals the caller deliberately ignores (nohup ignores SIGHUP) stay ignored.
    """
    def terminate(signum, frame):
        raise SystemExit(128 + signum)

    for signum in (signal.SIGHUP, signal.SIGTERM):
        if signal.getsignal(signum) == signal.SIG_DFL:
            signal.signal(signum, terminate)


def main():
    unwind_on_termination()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("env_file", type=Path, help="deployment .env file")
    parser.add_argument("--lock-fd", type=int, help=argparse.SUPPRESS)
    args = parser.parse_args()
    path = args.env_file
    if path.is_symlink() or not path.is_file():
        sys.exit("Backup key initialization requires a regular .env file.")
    path = path.absolute()
    # Lock a stable sidecar: replacing .env atomically must not replace the lock inode.
    # Keep the sidecar after unlocking so waiting initializers all use the same lock.
    lock_path = path.with_name(path.name + ".lock")
    lock = args.lock_fd
    if lock is None:
        lock = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        owner = path.stat()
        lock_owner = os.fstat(lock)
        if (lock_owner.st_uid, lock_owner.st_gid) != (owner.st_uid, owner.st_gid):
            os.fchown(lock, owner.st_uid, owner.st_gid)
    elif not os.path.samestat(os.fstat(lock), lock_path.lstat()):
        sys.exit("The inherited lock does not belong to this .env file.")
    with os.fdopen(lock, "w") as guard:
        # An inherited descriptor already holds the parent's lock.
        if args.lock_fd is None:
            fcntl.flock(guard, fcntl.LOCK_EX)
        if path.is_symlink() or not path.is_file():
            sys.exit("Backup key initialization requires a regular .env file.")
        contents = path.read_text()
        try:
            validate_dotenv(contents)
        except ValueError as error:
            sys.exit(str(error))
        value = resolve_key(path)
        if value:
            if not re.fullmatch(r"[0-9a-f]{64}", value):
                sys.exit("BACKUP_GPG_PASSPHRASE must be exactly 64 lowercase hexadecimal characters.")
            path.chmod(0o600)
            return
        pattern = re.compile(r"^[ \t]*(?:export[ \t]+)?BACKUP_GPG_PASSPHRASE[ \t]*[=:]")
        key = secrets.token_hex(32)
        lines = contents.splitlines(keepends=True)
        found = any(pattern.match(line) for line in lines)
        updated = "".join("BACKUP_GPG_PASSPHRASE=" + key + "\n" if pattern.match(line) else line
                          for line in lines)
        if not found:
            updated += "\nBACKUP_GPG_PASSPHRASE=" + key + "\n"
        owner = path.stat()
        fd, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
        try:
            with os.fdopen(fd, "w") as output:
                current = os.fstat(output.fileno())
                if (current.st_uid, current.st_gid) != (owner.st_uid, owner.st_gid):
                    os.fchown(output.fileno(), owner.st_uid, owner.st_gid)
                os.fchmod(output.fileno(), stat.S_IRUSR | stat.S_IWUSR)
                output.write(updated)
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    print("Generated backup encryption key in .env. Save it separately in a password manager; it is required for recovery.")


if __name__ == "__main__":
    main()

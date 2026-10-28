#!/usr/bin/env python3
"""Initialize a deployment backup passphrase without executing its .env file."""

import argparse
import fcntl
import os
from pathlib import Path
import re
import secrets
import stat
import sys
import tempfile


# Kept in sync with manage.py: both helpers ship as standalone release files.
def validate_dotenv(text):
    """Require quoted dotenv values to end on the same physical line."""
    assignment = re.compile(r"^[ \t]*(?:export[ \t]+)?[A-Za-z_][A-Za-z0-9_]*[ \t]*=[ \t]*(.*)$")
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
                escaped = True
            elif character == value[0]:
                break
        else:
            raise ValueError(f"Multiline or unterminated quoted .env value at line {number}; use a single-line value.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("env_file", type=Path, help="deployment .env file")
    path = parser.parse_args().env_file
    if path.is_symlink() or not path.is_file():
        sys.exit("Backup key initialization requires a regular .env file.")
    # Lock a stable sidecar: replacing .env atomically must not replace the lock inode.
    # Keep the sidecar after unlocking so waiting initializers all use the same lock.
    lock = os.open(str(path) + ".lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    owner = path.stat()
    lock_owner = os.fstat(lock)
    if (lock_owner.st_uid, lock_owner.st_gid) != (owner.st_uid, owner.st_gid):
        os.fchown(lock, owner.st_uid, owner.st_gid)
    with os.fdopen(lock, "w") as guard:
        fcntl.flock(guard, fcntl.LOCK_EX)
        if path.is_symlink() or not path.is_file():
            sys.exit("Backup key initialization requires a regular .env file.")
        contents = path.read_text()
        try:
            validate_dotenv(contents)
        except ValueError as error:
            sys.exit(str(error))
        path.chmod(0o600)
        pattern = re.compile(r"^[ \t]*(?:export[ \t]+)?BACKUP_GPG_PASSPHRASE[ \t]*=")
        value = ""
        for line in contents.splitlines():
            match = pattern.match(line)
            if match:
                value = re.sub(r"\s+#.*$", "", line[match.end():]).strip()
        if value not in ("", '""', "''") and not value.startswith("#"):
            sys.exit(0)
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

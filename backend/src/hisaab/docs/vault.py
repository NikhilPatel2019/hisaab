"""Write-once, content-addressed storage for original files.

Each file is stored under its SHA-256, exactly as received, and made read-only. Storing the
same bytes twice is a no-op, and every read is checked against the hash, so what comes back
is byte-for-byte what came in.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path


class VaultError(Exception):
    pass


def sha256_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class Vault:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def path_for(self, sha256: str) -> Path:
        if len(sha256) != 64 or any(c not in "0123456789abcdef" for c in sha256):
            raise VaultError("Not a SHA-256.")
        return self.root / sha256[:2] / sha256[2:4] / f"{sha256}.bin"

    def has(self, sha256: str) -> bool:
        return self.path_for(sha256).is_file()

    def put(self, data: bytes) -> str:
        sha = sha256_of(data)
        target = self.path_for(sha)
        if target.is_file():
            return sha
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=".incoming-")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            os.chmod(tmp, 0o444)
            try:
                os.link(tmp, target)  # fails if it already exists: never overwrite
            except FileExistsError:
                pass
            except OSError:
                if not target.exists():
                    os.replace(tmp, target)
        finally:
            if os.path.exists(tmp):
                # tmp may be a hard link to target now, so never chmod it on POSIX.
                try:
                    os.unlink(tmp)
                except PermissionError:  # Windows won't delete read-only files
                    os.chmod(tmp, 0o644)
                    os.unlink(tmp)
                    if target.exists():
                        os.chmod(target, 0o444)
        return sha

    def get(self, sha256: str) -> bytes:
        path = self.path_for(sha256)
        try:
            data = path.read_bytes()
        except FileNotFoundError as exc:
            raise VaultError(
                f"The original file {sha256[:12]}… is missing from the vault."
            ) from exc
        if sha256_of(data) != sha256:
            raise VaultError(f"The original file {sha256[:12]}… doesn't match its fingerprint.")
        return data

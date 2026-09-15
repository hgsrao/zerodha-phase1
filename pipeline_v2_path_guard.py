"""External path policy for Pipeline V2; frozen Certifier V2 is never modified."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

FROZEN_CERTIFIER_V2_SOURCE_SHA256 = (
    "0FBB4E8000D18F951C508DF179DD4A27D9A31F4D5A36C77F62A0D4DEAC7F0568"
)
APPROVED_STATUSES = frozenset(("PASS", "PASS_WITH_SOURCE_FLAGS"))


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def is_allowed_path(path: str | Path, cert_result: Any) -> bool:
    """Return true only when the existing file is exactly bound to frozen V2 certification."""
    candidate = Path(path).resolve()
    if not candidate.is_file() or cert_result is None:
        return False
    if getattr(cert_result, "status", None) not in APPROVED_STATUSES:
        return False
    certified_sha = getattr(cert_result, "file_sha256", "")
    certifier_sha = getattr(cert_result, "certifier_sha256", "")
    if not isinstance(certified_sha, str) or not isinstance(certifier_sha, str):
        return False
    return (
        certifier_sha.lower() == FROZEN_CERTIFIER_V2_SOURCE_SHA256.lower()
        and _sha256_file(candidate).lower() == certified_sha.lower()
    )

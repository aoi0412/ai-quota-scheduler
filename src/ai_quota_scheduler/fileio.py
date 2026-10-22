"""Small file helpers shared by the adapters and the control protocol."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

__all__ = ["atomic_write_json"]


def atomic_write_json(path: Path, data: Any) -> None:
    """Write ``data`` to ``path`` via a temp file and rename.

    Readers see either the old file or the complete new one, never a prefix.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise

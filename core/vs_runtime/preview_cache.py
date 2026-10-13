"""Widget-owned preview cache directories, separate from the project cache root."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path


class PreviewCache:
    """Own one unique directory; release it only after its worker jobs retire.

    Never prune sibling directories: they may belong to another live widget or
    process. A crashed session cannot collide with a newly allocated directory.
    """

    def __init__(self, root: Path):
        root.mkdir(parents=True, exist_ok=True)
        self._directory = tempfile.TemporaryDirectory(
            prefix="session-", dir=root, ignore_cleanup_errors=True
        )
        self.path = Path(self._directory.name).resolve()

    def cleanup(self) -> None:
        """Delete this cache, including script-created files, after worker unload."""
        self._directory.cleanup()


def clear_preview_cache(root: Path) -> None:
    """Offline removal of an entire preview cache root, including crash remnants.

    Close all application instances and preview workers using this root before
    calling. Pass the preview cache directory, never the project directory.
    This explicit maintenance operation is never run during session allocation.
    """
    try:
        shutil.rmtree(root)
    except FileNotFoundError:
        pass


__all__ = ["PreviewCache", "clear_preview_cache"]

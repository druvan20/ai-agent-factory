from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

from app.config import get_settings


class FileStore:
    """Single abstraction for all local filesystem operations.

    Layout:
        data/projects/{project_id}/uploads/{doc_id}/{filename}
        data/projects/{project_id}/generated/...
        data/projects/{project_id}/graphs/...
    """

    def __init__(self) -> None:
        self._root = get_settings().base_data_dir

    def _project_dir(self, project_id: str) -> Path:
        return self._root / project_id

    def uploads_dir(self, project_id: str, doc_id: str) -> Path:
        d = self._project_dir(project_id) / "uploads" / doc_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def generated_dir(self, project_id: str) -> Path:
        d = self._project_dir(project_id) / "generated"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def graphs_dir(self, project_id: str) -> Path:
        d = self._project_dir(project_id) / "graphs"
        d.mkdir(parents=True, exist_ok=True)
        return d

    async def save_upload(
        self, project_id: str, doc_id: str, filename: str, content: bytes
    ) -> tuple[str, str]:
        """Save upload bytes, return (storage_path, content_hash)."""
        dest_dir = self.uploads_dir(project_id, doc_id)
        dest = dest_dir / filename
        dest.write_bytes(content)
        content_hash = hashlib.sha256(content).hexdigest()
        return str(dest.relative_to(self._root)), content_hash

    def read_upload(self, storage_path: str) -> bytes:
        full = self._root / storage_path
        if not full.exists():
            raise FileNotFoundError(f"File not found: {storage_path}")
        return full.read_bytes()

    def delete_project_files(self, project_id: str) -> None:
        d = self._project_dir(project_id)
        if d.exists():
            shutil.rmtree(d)


file_store = FileStore()
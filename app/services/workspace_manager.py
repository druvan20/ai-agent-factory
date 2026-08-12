"""Workspace manager for W3 code generation.

Handles:
  - Per-run workspace directory creation
  - Writing generated code files
  - Building MANIFEST.json (task_id -> file paths mapping)
  - Zipping the workspace into a downloadable bundle
"""
from __future__ import annotations

import json
import logging
import shutil
import zipfile
from pathlib import Path

from app.config import get_settings

logger = logging.getLogger(__name__)


class WorkspaceManager:

    def __init__(self) -> None:
        self._root = get_settings().base_data_dir

    def workspace_dir(self, project_id: str, run_id: str) -> Path:
        d = self._root / project_id / "codegen" / run_id / "workspace"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def bundle_dir(self, project_id: str, run_id: str) -> Path:
        d = self._root / project_id / "codegen" / run_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def write_file(self, project_id: str, run_id: str, rel_path: str, content: str) -> Path:
        ws = self.workspace_dir(project_id, run_id)
        full = ws / rel_path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content, encoding="utf-8")
        logger.debug("Wrote %d chars to %s", len(content), full)
        return full

    def write_manifest(
        self, project_id: str, run_id: str, manifest: dict
    ) -> Path:
        ws = self.workspace_dir(project_id, run_id)
        path = ws / "MANIFEST.json"
        path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        logger.info("MANIFEST.json written: %d tasks", len(manifest.get("tasks", {})))
        return path

    def create_bundle(self, project_id: str, run_id: str) -> str:
        """Zip the workspace and return the path relative to base_data_dir."""
        ws = self.workspace_dir(project_id, run_id)
        bd = self.bundle_dir(project_id, run_id)
        zip_path = bd / "bundle.zip"

        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for fp in ws.rglob("*"):
                if fp.is_file():
                    arcname = fp.relative_to(ws)
                    zf.write(fp, arcname)

        rel = str(zip_path.relative_to(self._root))
        logger.info("Bundle created: %s (%d bytes)", rel, zip_path.stat().st_size)
        return rel

    def get_bundle_absolute(self, rel_path: str) -> Path:
        return self._root / rel_path

    def cleanup(self, project_id: str, run_id: str) -> None:
        d = self._root / project_id / "codegen" / run_id
        if d.exists():
            shutil.rmtree(d)


workspace_manager = WorkspaceManager()
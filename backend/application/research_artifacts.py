"""Authorize an immutable checkpoint attachment before any storage I/O."""

import re
from uuid import UUID

from application.errors import AppError
from application.ports import ArtifactStorePort
from application.research_queries import ResearchQueries
from domain.ports import AdapterError
from domain.research.facts import AnalysisArtifact
from domain.research.reporting import artifact_files

MEDIA_TYPES = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
    "pdf": "application/pdf",
    "csv": "text/csv",
    "json": "application/json",
    "txt": "text/plain",
}


class ResearchArtifacts:
    def __init__(self, queries: ResearchQueries, storage: ArtifactStorePort):
        self.queries, self.storage = queries, storage

    async def download(self, owner: UUID, session_id: UUID, artifact_id: str, name: str):
        if (
            not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", artifact_id)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", name)
            or name.rsplit(".", 1)[-1].lower() not in MEDIA_TYPES
        ):
            raise AppError("artifact_not_found", "Attachment not found")
        try:
            view = await self.queries.checkpoint_view(owner, session_id)
        except AppError as exc:
            if exc.code == "checkpoint_not_found":
                raise AppError("artifact_not_found", "Attachment not found") from None
            raise
        raw = view["state"]["analysis_artifacts"].get(artifact_id)
        if raw is None:
            raise AppError("artifact_not_found", "Attachment not found")
        artifact = AnalysisArtifact.model_validate(raw)
        try:
            names = artifact_files(artifact)
        except ValueError:
            raise AppError(
                "content_unavailable", "Attachment is unavailable", retryable=True
            ) from None
        if artifact.execution_status != "completed" or name not in names:
            raise AppError("artifact_not_found", "Attachment not found")
        key = artifact.object_keys[names.index(name)]
        # A registered key still cannot cross the current Run/Artifact namespace.
        if not key.startswith(f"analysis/{view['run_id']}/{artifact_id}/"):
            raise AppError("content_unavailable", "Attachment is unavailable", retryable=True)
        try:
            body = await self.storage.read(key)
        except (AdapterError, ValueError):
            raise AppError(
                "content_unavailable", "Attachment is unavailable", retryable=True
            ) from None
        if len(body) > 10 * 1024 * 1024:
            raise AppError("content_unavailable", "Attachment is unavailable", retryable=True)
        return body, MEDIA_TYPES[name.rsplit(".", 1)[-1].lower()]

"""Internal index DTOs, never accepted as public authorization inputs."""

from typing import Annotated
from uuid import UUID

from pydantic import Field, StrictFloat, StrictInt, model_validator

from application.knowledge_models import ChunkType
from domain.research.models import Positive, Record, Text

Scalar = Annotated[StrictFloat, Field(allow_inf_nan=False)]
Weight = Annotated[Scalar, Field(gt=0)]
Token = Annotated[StrictInt, Field(ge=0, le=4294967295)]
Profile = Annotated[Text, Field(max_length=128)]


class IndexEmbedding(Record):
    dense_vector: Annotated[list[Scalar], Field(min_length=1024, max_length=1024)]
    sparse_vector: Annotated[dict[Token, Weight], Field(min_length=1, max_length=8192)]

    @model_validator(mode="after")
    def nonzero(self):
        if not any(self.dense_vector):
            raise ValueError("Cosine embedding cannot be zero")
        return self


class IndexRow(IndexEmbedding):
    chunk_id: Annotated[Text, Field(max_length=256)]
    kb_id: UUID
    document_id: UUID
    document_version_id: UUID
    index_version: Profile
    chunk_type: ChunkType
    page_start: Positive | None
    year: Annotated[StrictInt, Field(ge=1900, le=2100)] | None


class VectorScope(Record):
    kb_id: UUID
    version_ids: Annotated[list[UUID], Field(min_length=1, max_length=100)]
    index_version: Profile
    document_ids: Annotated[list[UUID], Field(max_length=100)] = Field(default_factory=list)
    chunk_types: Annotated[list[ChunkType], Field(max_length=3)] = Field(default_factory=list)
    year_min: Annotated[StrictInt, Field(ge=1900, le=2100)] | None = None
    year_max: Annotated[StrictInt, Field(ge=1900, le=2100)] | None = None

    @model_validator(mode="after")
    def valid_scope(self):
        for values in [self.version_ids, self.document_ids, self.chunk_types]:
            if len(set(values)) != len(values):
                raise ValueError("Duplicate scope IDs")
        if (
            self.year_min is not None
            and self.year_max is not None
            and self.year_min > self.year_max
        ):
            raise ValueError("Inverted year bounds")
        return self

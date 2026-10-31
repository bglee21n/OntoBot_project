"""ORM 모델 — TRD 6절 묶음별 파일. Alembic 이 메타데이터를 읽도록 전부 여기서 import한다."""

from ontobot.db.models import (  # noqa: F401
    actions,
    chat,
    documents,
    evaluation,
    jobs,
    objects,
    ontology,
    security,
    settings,
    sync,
    workshop,
)
from ontobot.db.models.base import Base

__all__ = ["Base"]

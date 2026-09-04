"""Entity extraction, confidence scoring and entity resolution."""

from __future__ import annotations

from app.correlation.confidence import (
    ConfidenceAssessment,
    ConfidenceEngine,
    ConfidenceRule,
    ConfidenceSignal,
    classify,
    default_engine,
)
from app.correlation.extraction import (
    EntityDraft,
    ExtractionResult,
    RelationshipDraft,
    extract,
)
from app.correlation.resolver import EntityResolver, ResolutionSummary

__all__ = [
    "ConfidenceAssessment",
    "ConfidenceEngine",
    "ConfidenceRule",
    "ConfidenceSignal",
    "EntityDraft",
    "EntityResolver",
    "ExtractionResult",
    "RelationshipDraft",
    "ResolutionSummary",
    "classify",
    "default_engine",
    "extract",
]

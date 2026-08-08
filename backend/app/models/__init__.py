"""Database models."""
"""SQLAlchemy model registry.

Importing this package registers every mapped class on the shared metadata.
"""

from app.models.project import Project
from app.models.user import (
    Consent,
    Conversation,
    Message,
    MessageRole,
    TrainingStatus,
    User,
    UserSettings,
    VoiceProfile,
    VoiceSample,
)
from app.models.memory import (
    CompanionMemoryProvenance,
    Legacy,
    LegacyStatus,
    Memory,
    MemoryContradictionGroup,
    MemoryExtractionRun,
    MemoryExtractionRunStatus,
    MemoryLink,
    MemoryParticipant,
    MemoryProvenance,
    MemoryReviewStatus,
    MemoryRevision,
    MemoryTag,
    MemoryType,
    StoryMessage,
    StoryMessageRole,
    StorySession,
    StorySessionStatus,
    Tag,
)

__all__ = [
    "CompanionMemoryProvenance",
    "Consent",
    "Conversation",
    "Legacy",
    "LegacyStatus",
    "Memory",
    "MemoryContradictionGroup",
    "MemoryExtractionRun",
    "MemoryExtractionRunStatus",
    "MemoryLink",
    "MemoryParticipant",
    "MemoryProvenance",
    "MemoryReviewStatus",
    "MemoryRevision",
    "MemoryTag",
    "MemoryType",
    "Message",
    "MessageRole",
    "Project",
    "StoryMessage",
    "StoryMessageRole",
    "StorySession",
    "StorySessionStatus",
    "Tag",
    "TrainingStatus",
    "User",
    "UserSettings",
    "VoiceProfile",
    "VoiceSample",
]

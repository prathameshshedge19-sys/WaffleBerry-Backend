"""Database models."""
"""SQLAlchemy model registry.

Importing this package registers every mapped class on the shared metadata.
"""

from app.models.project import Project
from app.models.auth_challenge import AuthChallenge
from app.models.user import (
    Consent,
    Conversation,
    Message,
    MessageRole,
    PlanTier,
    TrainingStatus,
    User,
    UserSettings,
    VoiceProfile,
    VoiceSample,
)
from app.models.quota import UserDailyUsage
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
    "AuthChallenge",
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
    "PlanTier",
    "Project",
    "StoryMessage",
    "StoryMessageRole",
    "StorySession",
    "StorySessionStatus",
    "Tag",
    "TrainingStatus",
    "User",
    "UserDailyUsage",
    "UserSettings",
    "VoiceProfile",
    "VoiceSample",
]

from app.models.auth_challenge import AuthChallenge
from app.models.access import AccessRole, InviteStatus, LegacyAccessEvent, LegacyAccessInvite
from app.models.collaboration import CollaboratorStatus, LegacyCollaborator
from app.models.conversation import Conversation, Message, MessageRole
from app.models.legacy import Legacy, LegacySetupStatus
from app.models.memory import Memory, MemoryEntity, MemoryEntityLink, MemoryOperation, MemoryRevision, MemoryStatus
from app.models.progress import BuilderActivity, DailyPrompt, PromptStatus
from app.models.user import User
from app.models.web_source import MessageWebSource
from app.models.viewer import LegacyViewerAccess, ViewerAccessStatus
from app.models.visitor import LegacyVisitorProfile, VisitorRelationshipStatus

__all__ = ["AccessRole", "AuthChallenge", "BuilderActivity", "CollaboratorStatus", "Conversation", "DailyPrompt", "InviteStatus", "Legacy", "LegacyAccessEvent", "LegacyAccessInvite", "LegacyCollaborator", "LegacySetupStatus", "LegacyViewerAccess", "LegacyVisitorProfile", "Memory", "MemoryEntity", "MemoryEntityLink", "MemoryOperation", "MemoryRevision", "MemoryStatus", "Message", "MessageRole", "MessageWebSource", "PromptStatus", "User", "ViewerAccessStatus", "VisitorRelationshipStatus"]

# L13 isolated derived projection and transactional canonical-memory invalidation.
from app.models.personality import LegacyPersonalityProfile  # noqa: F401
from app.services import personality_invalidation  # noqa: F401

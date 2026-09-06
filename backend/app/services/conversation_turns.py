"""Internal turn coordination after route authorization and user-message staging.

These are server-only contracts, not request schemas or authorization tokens.
Routes still own access checks, setup, message commits and HTTP/SSE serialization.
Durable lifecycle admission and receipts are provided separately by turn_lifecycle.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal, cast

from sqlalchemy.orm import Session

from app.services import turn_observability as obs, usage_accounting as usage
from app.models.conversation import Conversation, Message, MessageRole
from app.models.legacy import Legacy
from app.services.memory import MemoryProvider
from app.services.rya import ChatTurn
from app.services.web_search import WebSearchProvider, WebSearchResult

if TYPE_CHECKING:
    from app.services.builder_turns import BuilderPreparedTurn
    from app.services.persona_turns import PersonaPreparedTurn

TurnMode = Literal["rya", "legacy"]
TurnRole = Literal["owner", "collaborator", "viewer"]
InputMode = Literal["text", "voice"]


@dataclass(frozen=True)
class TurnCapabilities:
    apply_builder_memory: bool
    read_only_retrieval: bool
    current_information: bool


@dataclass(frozen=True)
class TurnActorContext:
    conversation_id: int
    legacy_id: int
    mode: TurnMode
    actor_id: int
    role: TurnRole
    user_message_id: int
    content: str = field(repr=False)
    input_mode: InputMode = "text"
    timezone_name: str = "UTC"

    @classmethod
    def from_authorized(cls, conversation: Conversation, legacy: Legacy, user_message: Message,
                        *, actor_id: int, role: str, input_mode: str = "text", timezone_name: str = "UTC") -> TurnActorContext:
        """Use resolved server rows/role only; never unpack a request into this type."""
        context = cls(conversation.id, legacy.id, cast(TurnMode, conversation.mode), actor_id, cast(TurnRole, role),
                      user_message.id, user_message.content, cast(InputMode, input_mode), timezone_name)
        context.validate_scope(conversation, legacy, user_message)
        return context

    @property
    def capabilities(self) -> TurnCapabilities:
        # Permissions cannot be supplied independently of the server mode/role.
        if self.mode == "rya" and self.role in {"owner", "collaborator"}:
            return TurnCapabilities(True, False, False)
        if self.mode == "legacy" and self.role == "viewer":
            return TurnCapabilities(False, True, True)
        raise ValueError("Invalid turn mode/role")

    def validate_scope(self, conversation: Conversation, legacy: Legacy, user_message: Message) -> None:
        self.capabilities
        if (self.conversation_id != conversation.id or self.legacy_id != legacy.id
                or conversation.legacy_id != legacy.id or self.mode != conversation.mode
                or self.actor_id != conversation.user_id
                or self.user_message_id != user_message.id or user_message.id is None
                or user_message.conversation_id != conversation.id or user_message.role != MessageRole.USER
                or self.content != user_message.content):
            raise ValueError("Turn scope does not match authorized rows")
        if self.mode == "rya" and (self.role == "owner") != (legacy.owner_user_id == self.actor_id):
            raise ValueError("Builder role does not match Legacy owner")


@dataclass(frozen=True)
class PreparedTurn:
    actor: TurnActorContext
    turns: list[ChatTurn] = field(repr=False)


@dataclass(frozen=True)
class TurnCompletionContext:
    """Rows already persisted by the route at its existing success boundary."""
    conversation: Conversation
    legacy: Legacy
    user_message: Message
    assistant_message: Message

    def validate(self, actor: TurnActorContext) -> None:
        actor.validate_scope(self.conversation, self.legacy, self.user_message)
        if (self.assistant_message.id is None
                or self.assistant_message.conversation_id != actor.conversation_id
                or self.assistant_message.role != MessageRole.ASSISTANT):
            raise ValueError("Completion requires a persisted assistant in this conversation")


@dataclass(frozen=True)
class TurnCompletionResult:
    memories_saved: int = 0
    progress: dict | None = None
    streak: dict | None = None
    today_just_completed: bool = False


@obs.timed("preparation")
async def prepare_turn(db: Session, actor: TurnActorContext, conversation: Conversation,
                       legacy: Legacy, user_message: Message, memory_provider: MemoryProvider,
                       *, activated_now: bool = False) -> BuilderPreparedTurn | PersonaPreparedTurn:
    actor.validate_scope(conversation, legacy, user_message)
    obs.metadata(mode=actor.mode, role=actor.role, input_mode=actor.input_mode)
    memory_provider = usage.wrap_memory(memory_provider)
    if actor.mode == "rya":
        from app.services.builder_turns import prepare_builder_turn
        return await prepare_builder_turn(db, actor, conversation, legacy, user_message, memory_provider, activated_now=activated_now)
    from app.services.persona_turns import prepare_persona_turn
    return await prepare_persona_turn(db, actor, conversation, legacy, user_message, memory_provider)


async def prepare_current_information(prepared: PreparedTurn, provider: WebSearchProvider) -> WebSearchResult | None:
    # Kept separate so SSE still emits start/activity before making the web call.
    from app.services.persona_turns import PersonaPreparedTurn, add_current_information
    if not isinstance(prepared, PersonaPreparedTurn) or not prepared.actor.capabilities.current_information:
        raise ValueError("Current information requires a persona turn")
    return await add_current_information(prepared, provider)


@obs.timed("post_turn_effects")
async def complete_turn(db: Session, prepared: PreparedTurn, completion: TurnCompletionContext,
                        *, include_progress: bool = False) -> TurnCompletionResult:
    completion.validate(prepared.actor)
    if prepared.actor.mode == "legacy":
        from app.services.persona_turns import PersonaPreparedTurn
        if not isinstance(prepared, PersonaPreparedTurn):
            raise ValueError("Invalid persona preparation")
        return TurnCompletionResult()  # No visitor domain writes, even if asked for progress.
    from app.services.builder_turns import complete_builder_turn
    return await complete_builder_turn(db, prepared, completion, include_progress=include_progress)

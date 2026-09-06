"""L14 internal read-only tools; no route or provider registers these contracts.

The server binds a claimed durable turn, never model-supplied security fields.
Each call uses fresh read sessions, and checks authorization again before output.
No tool applies memory, identity, personality, activity or lifecycle mutations.
"""
from dataclasses import dataclass, field
import json
import re
from types import MappingProxyType
from urllib.parse import urlparse

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.config import get_settings
from app.services import turn_observability as obs, usage_accounting as usage
from app.models.conversation import Conversation, Message
from app.models.legacy import Legacy
from app.models.memory import MemoryEntity
from app.models.turn import ConversationTurn
from app.models.visitor import LegacyVisitorProfile
from app.schemas.conversation_tools import (
    EmptyArguments, QueryArguments, MemoryArguments, ToolErrorCode,
    MAX_ARGUMENT_BYTES, MAX_RESULT_BYTES, MAX_CUES, MAX_SOURCES,
)
from app.services.authorization import legacy_role, require_persona_legacy
from app.services.conversation_turns import TurnActorContext
from app.services.legacy_intelligence import analyze_legacy_query
from app.services.legacy_persona import nickname_cadence_guard
from app.services.memory import LivingMemoryService, MemoryProviderError
from app.services.personality_style import select_personality_style
from app.services.visitor_identity import current_language, visitor_evidence
from app.services.web_search import WebSearchError, minimize_search_query


REGISTRY = MappingProxyType({
    "retrieve_legacy_memories": MemoryArguments,
    "get_legacy_personality": EmptyArguments,
    "get_visitor_relationship_context": EmptyArguments,
    "get_current_information": QueryArguments,
})
CAPABILITIES = MappingProxyType({
    ("rya", "owner"): frozenset({"retrieve_legacy_memories"}),
    ("rya", "collaborator"): frozenset({"retrieve_legacy_memories"}),
    ("legacy", "viewer"): frozenset(REGISTRY),
})


class _ToolFailure(Exception):
    def __init__(self, code: ToolErrorCode):
        self.code = code


def _error(code: ToolErrorCode):
    return {"ok": False, "error": {"code": code}}


def _json(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def _clip(value, byte_limit):
    return str(value or "").encode("utf-8")[:byte_limit].decode("utf-8", errors="ignore")


@dataclass(frozen=True)
class TurnToolContext:
    """Server-only binding; not a request schema or independently trusted token."""
    actor: TurnActorContext = field(repr=False)
    turn_id: int
    claim_token: str = field(repr=False)
    request_digest: str = field(repr=False)

    @property
    def tool_names(self):
        return CAPABILITIES.get((self.actor.mode, self.actor.role), frozenset())

    @classmethod
    def from_turn(cls, db, actor: TurnActorContext, turn_id: int):
        # Bind only in the Phase C processing owner, after durable user linkage.
        # Reading a streaming row's IDs must not mint a claim for another worker.
        claim_token = db.info.get("turn_claim_token")
        if db.info.get("active_turn_id") != turn_id or not claim_token:
            raise ValueError("Invalid tool scope")
        turn = db.get(ConversationTurn, turn_id, populate_existing=True)
        if turn is None or turn.claim_token != claim_token:
            raise ValueError("Invalid tool scope")
        context = cls(actor, turn_id, claim_token, turn.request_digest)
        try:
            _authorize(db, context)
        except Exception:
            raise ValueError("Invalid tool scope") from None
        return context


@dataclass(frozen=True)
class ToolOutput:
    """Result binding stays server-side; adapters must present the expected turn.

    No cached payload from Turn A may be delivered as a result for Turn B.
    The returned JSON dictionary contains data only, with no server claim token.
    """
    _context: TurnToolContext = field(repr=False)
    _encoded: str = field(repr=False)

    def for_turn(self, context: TurnToolContext) -> dict:
        if not isinstance(context, TurnToolContext) or context != self._context:
            return _error("tool_scope_invalid")
        return json.loads(self._encoded)


def _authorize(db, context):
    if not isinstance(context, TurnToolContext) or not context.tool_names:
        raise _ToolFailure("tool_scope_invalid")
    actor = context.actor
    turn = db.get(ConversationTurn, context.turn_id, populate_existing=True)
    if (turn is None or turn.state != "streaming" or not turn.claim_token
            or turn.claim_token != context.claim_token or turn.request_digest != context.request_digest
            or (turn.actor_user_id, turn.conversation_id, turn.legacy_id, turn.mode, turn.user_message_id, turn.input_mode)
            != (actor.actor_id, actor.conversation_id, actor.legacy_id, actor.mode, actor.user_message_id, actor.input_mode)):
        raise _ToolFailure("tool_scope_invalid")
    conversation = db.get(Conversation, actor.conversation_id, populate_existing=True)
    legacy = db.get(Legacy, actor.legacy_id, populate_existing=True)
    message = db.get(Message, actor.user_message_id, populate_existing=True)
    if conversation is None or legacy is None or message is None:
        raise _ToolFailure("tool_scope_invalid")
    try:
        actor.validate_scope(conversation, legacy, message)
        if actor.mode == "legacy":
            require_persona_legacy(db, actor.actor_id, legacy.id)
        elif legacy_role(db, actor.actor_id, legacy) != actor.role:
            obs.degraded("access_changed")
            raise ValueError("Access changed")
    except Exception as error:
        if obs.error_category(error) == "authorization_denied":
            obs.degraded("access_changed")
        raise _ToolFailure("tool_scope_invalid") from None
    return legacy


@obs.timed("history_loading")
def _recent(db, context):
    return db.scalars(select(Message).where(Message.conversation_id == context.actor.conversation_id)
        .order_by(Message.id.desc()).limit(get_settings().ai_max_context_messages)).all()


@obs.timed("relationship_context")
def _visitor(db, context, active):
    profile = db.scalar(select(LegacyVisitorProfile).where(
        LegacyVisitorProfile.legacy_id == context.actor.legacy_id,
        LegacyVisitorProfile.viewer_user_id == context.actor.actor_id))
    if profile and profile.matched_entity_id is not None:
        entity = db.get(MemoryEntity, profile.matched_entity_id)
        if entity is None or entity.legacy_id != context.actor.legacy_id:
            raise _ToolFailure("tool_scope_invalid")
    visitor = visitor_evidence(active, profile)
    visitor["current_turn_language"] = current_language(context.actor.content)
    return visitor


def _verified(visitor):
    return (visitor.get("relationship_status") == "verified_from_memory"
            and not visitor.get("claim_conflicts_with_memory")
            and visitor.get("claimed_relationship") in visitor.get("supported_relationships", ()))


def _visible_memories(context, memories, visitor):
    if context.actor.mode == "legacy" and not _verified(visitor):
        # Same private-address boundary as persona_system_context; no prompts are
        # rendered or returned. Verification is also checked against active data.
        return [m for m in memories if not re.search(r"\b(calls?|called|nickname|pet name|addressed as)\b", m.canonical_text, re.I)]
    return list(memories)


class ConversationTools:
    def __init__(self, sessions, memory_provider, web_provider=None):
        self.sessions = sessions
        self.memory = LivingMemoryService(usage.wrap_memory(memory_provider))
        self.web = web_provider

    @obs.observe_tool
    async def execute(self, context: TurnToolContext, name: str, arguments: dict) -> ToolOutput:
        try:
            if not isinstance(context, TurnToolContext):
                raise _ToolFailure("tool_scope_invalid")
            if not isinstance(name, str) or len(name) > 64 or not isinstance(arguments, dict):
                raise _ToolFailure("tool_invalid_arguments")
            if name not in REGISTRY or name not in context.tool_names:
                raise _ToolFailure("tool_not_allowed")
            try:
                encoded_arguments = _json(arguments).encode("utf-8")
                args = REGISTRY[name].model_validate(arguments)
            except (ValidationError, ValueError, TypeError, OverflowError, RecursionError):
                raise _ToolFailure("tool_invalid_arguments") from None
            if len(encoded_arguments) > MAX_ARGUMENT_BYTES:
                raise _ToolFailure("tool_invalid_arguments")
            # Separate fresh sessions avoid caller flushes, cached membership and
            # long-lived transaction snapshots. No commit or flush occurs here.
            with self.sessions() as db, db.no_autoflush:
                legacy = _authorize(db, context)
                active = self.memory.active_memories(db, legacy.id)
                with obs.stage("classification"):
                    route = analyze_legacy_query(context.actor.content, legacy.subject_name, active)
                obs.metadata(route=route.intent.value.lower())
                if name == "retrieve_legacy_memories":
                    if not route.needs_memory:
                        raise _ToolFailure("tool_not_allowed")
                    with obs.stage("memory_retrieval"):
                        ranked = await self.memory.retrieve_read_only(db, legacy.id, args.query, route)
                    selected_ids = [memory.id for memory in ranked]
                    data = None
                elif name == "get_legacy_personality":
                    visitor = _visitor(db, context, active)
                    retrieved = ()
                    if route.needs_memory:
                        with obs.stage("memory_retrieval"):
                            retrieved = await self.memory.retrieve_read_only(db, legacy.id, context.actor.content, route)
                    selected_ids = [memory.id for memory in retrieved]
                    data = None
                elif name == "get_visitor_relationship_context":
                    data = None
                else:
                    if not route.needs_fresh_data or self.web is None:
                        raise _ToolFailure("tool_current_info_unavailable")
                    query = self._public_query(context, args.query, legacy, active, _visitor(db, context, active))
                    with obs.stage("current_information"):
                        current = await usage.invoke("web", self.web.search, query)
                    data = self._web_result(current)
            # Recheck revocation/turn closure after any awaited provider work,
            # in a new transaction. Personal output is built from current rows.
            with self.sessions() as db, db.no_autoflush:
                legacy = _authorize(db, context)
                if name != "get_current_information":
                    active = self.memory.active_memories(db, legacy.id)
                    visitor = _visitor(db, context, active) if context.actor.mode == "legacy" else {}
                    if name == "retrieve_legacy_memories":
                        current_by_id = {m.id: m for m in _visible_memories(context, active, visitor)}
                        ranked = [current_by_id[i] for i in selected_ids if i in current_by_id][:args.max_results]
                        data = {"kind": "personal_evidence", "memories": [self._memory_record(m, legacy.id) for m in ranked]}
                    elif name == "get_legacy_personality":
                        current_by_id = {m.id: m for m in _visible_memories(context, active, visitor)}
                        retrieved = [current_by_id[i] for i in selected_ids if i in current_by_id]
                        recent = _recent(db, context)
                        with obs.stage("personality_selection"):
                            style = select_personality_style(db, legacy, context.actor.content, retrieved, visitor,
                                                             recent, history_order="newest_first")
                        obs.metadata(personality_status="available" if style is not None else "fallback")
                        expression = style.expression if style else None
                        if expression and len(expression.encode("utf-8")) > 256:
                            expression = None  # Never cut an original expression into a different one.
                        data = {"kind": "style_only", "authorizes_factual_claims": False,
                                "availability": "ready" if style is not None else "unavailable",
                                "phrasing_cues": [_clip(cue, 200) for cue in style.guidance[:MAX_CUES]] if style else [],
                                "optional_original_expression": expression, "max_expression_uses": 1}
                    else:
                        data = self._relationship(visitor, _recent(db, context))
            payload = {"ok": True, "data_is_untrusted": True, "data": data}
            if len(_json(payload).encode("utf-8")) > MAX_RESULT_BYTES:
                raise _ToolFailure("tool_data_unavailable")
        except _ToolFailure as exc:
            payload = _error(exc.code)
        except WebSearchError:
            payload = _error("tool_current_info_unavailable")
        except (MemoryProviderError, SQLAlchemyError):
            payload = _error("tool_data_unavailable")
        except Exception:
            # Never expose validation details, SQL, prompts, provider kind or repr.
            payload = _error("tool_internal_error")
        return ToolOutput(context, _json(payload))

    @staticmethod
    def _memory_record(memory, legacy_id):
        fact = _clip(memory.canonical_text, 768)
        return {"reference": memory.id, "text": fact, "truncated": fact != memory.canonical_text,
                "category": _clip(memory.category, 80), "story_reference": _clip(memory.story_key, 80) or None,
                "entities": [{"name": _clip(link.entity.name, 80), "relationship": _clip(link.role, 40)}
                             for link in memory.entity_links if link.entity.legacy_id == legacy_id][:2]}

    @staticmethod
    def _relationship(visitor, recent):
        verified = _verified(visitor)
        nicknames = visitor.get("visitor_specific_nicknames", ()) if verified else ()
        if nickname_cadence_guard(visitor, list(reversed(recent))):
            nicknames = ()
        return {"kind": "relationship_context", "identified": bool(visitor.get("identified")),
                "entity_match": "matched" if visitor.get("matched_entity_id") is not None else "unmatched",
                "preferred_name": _clip(visitor.get("preferred_name"), 128) or None,
                "claimed_relationship": _clip(visitor.get("claimed_relationship"), 80) or None,
                "relationship_status": "verified_from_memory" if verified else "unverified",
                "identity_proof": False, "claim_conflicts_with_memory": bool(visitor.get("claim_conflicts_with_memory")),
                "supported_relationships": [_clip(value, 40) for value in visitor.get("supported_relationships", ())][:3],
                "may_affirm_relationship": verified,
                "allowed_nicknames": [value for value in nicknames if len(value.encode("utf-8")) <= 64][:1],
                "max_nickname_uses": 1, "nickname_cooldown_assistant_turns": 2}

    @staticmethod
    def _public_query(context, query, legacy, active, visitor):
        # The model cannot append retrieved secrets or a transcript. It can only
        # repeat the existing minimizer's public query for this accepted message.
        accepted = minimize_search_query(context.actor.content)
        supplied = minimize_search_query(query)
        if not accepted or len(accepted) > 512 or supplied.casefold() != accepted.casefold():
            raise _ToolFailure("tool_not_allowed")
        public_route = analyze_legacy_query(accepted, legacy.subject_name, active)
        if public_route.needs_memory or not public_route.needs_fresh_data:
            raise _ToolFailure("tool_current_info_unavailable")
        private_names = [legacy.subject_name, visitor.get("preferred_name")]
        private_names.extend(value for memory in active for link in memory.entity_links
                             if link.entity.entity_type == "person" for value in (link.entity.name, *(link.entity.aliases or [])))
        if any(name and re.search(r"(?<!\w)" + re.escape(name) + r"(?!\w)", accepted, re.I) for name in private_names):
            raise _ToolFailure("tool_current_info_unavailable")
        return accepted

    @staticmethod
    def _web_result(current):
        sources = []
        for source in current.sources:
            parsed = urlparse(source.url)
            if (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password
                    or len(source.url.encode("utf-8")) > 1024 or any(ord(char) < 32 for char in source.url)):
                continue
            sources.append({"title": _clip(source.title, 180), "domain": _clip(parsed.hostname.removeprefix("www."), 255),
                            "url": source.url, "publication_date": _clip(source.publication_date, 40) or None})
            if len(sources) == MAX_SOURCES:
                break
        if not current.digest.strip() or not sources:
            raise _ToolFailure("tool_current_info_unavailable")
        digest = _clip(current.digest, 1600)
        return {"kind": "current_public_information", "digest": digest, "truncated": digest != current.digest,
                "sources": sources, "personal_evidence": False, "persisted": False}

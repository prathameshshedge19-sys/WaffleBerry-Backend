"""API routes for voice profiles and related endpoints."""

import asyncio
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from app.db import get_db
from app.dependencies.auth import get_current_user
from app.dependencies.ai import get_chat_service
from app.models.user import User
from app.schemas.user import (
    UserCreate, UserLogin, UserResponse, LoginResponse, VoiceProfileCreate, VoiceProfileResponse, 
    VoiceProfileUpdate, VoiceSampleCreate, VoiceSampleResponse,
    ConversationCreate, ConversationUpdate, ConversationResponse,
    MessageCreate, MessagePairResponse, MessageResponse
)
from app.crud.user import (
    UserCRUD, VoiceProfileCRUD, VoiceSampleCRUD, ConversationCRUD, MessageCRUD
)

from app.services.token_service import create_access_token
from app.services.ai.exceptions import (
    AIAuthenticationError,
    AIConfigurationError,
    AIConnectionError,
    AIInvalidResponseError,
    AIProviderUnavailableError,
    AIQuotaExceededError,
    AIRateLimitError,
    AIServiceError,
    AITimeoutError,
)

logger = logging.getLogger(__name__)


router = APIRouter()


def _sse_event(event: str, payload: dict) -> str:
    """Serialize one safely framed server-sent event."""
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event}\ndata: {data}\n\n"


def _safe_ai_error(exc: AIServiceError) -> tuple[int, str, str]:
    """Map an internal AI failure to HTTP status, safe code, and message."""
    if isinstance(exc, AIQuotaExceededError):
        return (
            status.HTTP_429_TOO_MANY_REQUESTS,
            exc.code,
            "Berry is temporarily unavailable because the AI usage balance "
            "has been exhausted.",
        )
    if isinstance(exc, AIRateLimitError):
        return (
            status.HTTP_429_TOO_MANY_REQUESTS,
            exc.code,
            "Berry is receiving too many requests right now. "
            "Please try again shortly.",
        )
    if isinstance(exc, (AIAuthenticationError, AIConfigurationError)):
        return (
            status.HTTP_503_SERVICE_UNAVAILABLE,
            exc.code,
            "Berry's AI service is not configured correctly.",
        )
    if isinstance(exc, AITimeoutError):
        return (
            status.HTTP_504_GATEWAY_TIMEOUT,
            exc.code,
            "Berry took too long to respond. Please try again.",
        )
    if isinstance(exc, AIConnectionError):
        return (
            status.HTTP_503_SERVICE_UNAVAILABLE,
            exc.code,
            "Berry could not reach the AI service. Please try again.",
        )
    if isinstance(exc, AIProviderUnavailableError):
        return (
            status.HTTP_503_SERVICE_UNAVAILABLE,
            exc.code,
            "Berry's AI service is temporarily unavailable. "
            "Please try again shortly.",
        )
    if isinstance(exc, AIInvalidResponseError):
        return (
            status.HTTP_502_BAD_GATEWAY,
            exc.code,
            "Berry's response was interrupted. Please try again.",
        )
    return (
        status.HTTP_503_SERVICE_UNAVAILABLE,
        getattr(exc, "code", "ai_service_error"),
        "I couldn't generate a response just now. Please try again.",
    )


def _ai_http_exception(exc: AIServiceError) -> HTTPException:
    http_status, code, safe_message = _safe_ai_error(exc)
    return HTTPException(
        status_code=http_status,
        detail={
            "code": code,
            "message": safe_message,
        },
    )


# ==================== USER ENDPOINTS ====================

@router.post("/users", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def create_user(user: UserCreate, db: Session = Depends(get_db)):
    """Create a new user account.
    
    - **full_name**: User's full name
    - **email**: User's email (must be unique)
    - **password**: Password (minimum 8 characters)
    """
    # Check if email already exists
    existing_user = UserCRUD.get_user_by_email(db, user.email)
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered"
        )
    
    db_user = UserCRUD.create_user(db, user)
    return db_user


@router.post("/login", response_model=LoginResponse)
async def login(user: UserLogin, db: Session = Depends(get_db)):
    """Authenticate a user with an email and password."""
    authenticated_user = UserCRUD.authenticate_user(db, user.email, user.password)
    if not authenticated_user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    access_token = create_access_token(authenticated_user.user_id)
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user": authenticated_user,
    }

@router.get("/me", response_model=UserResponse)
async def read_current_user(
    current_user: User = Depends(get_current_user),
):
    """Return the user authenticated by the Bearer access token."""
    return current_user

@router.get("/users/{user_id}", response_model=UserResponse)
async def get_user(user_id: int, db: Session = Depends(get_db)):
    """Get user details by ID."""
    user = UserCRUD.get_user(db, user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User with id {user_id} not found"
        )
    return user


# ==================== VOICE PROFILE ENDPOINTS ====================

@router.post("/voice-profiles", response_model=VoiceProfileResponse, status_code=status.HTTP_201_CREATED)
async def create_voice_profile(
    user_id: int,
    voice_profile: VoiceProfileCreate,
    db: Session = Depends(get_db)
):
    """Create a new voice profile for a user.
    
    This is the FIRST step in voice cloning:
    1. User creates a voice profile (Mom, Dad, Mentor, etc.)
    2. User uploads voice samples
    3. AI trains on the samples
    4. User can chat using that voice
    
    - **user_id**: The user creating this voice profile
    - **voice_name**: Name of the voice (e.g., "Mom", "Dad")
    - **relationship**: Relationship with the voice owner (e.g., "Mother", "Father")
    - **language**: Language spoken (default: English)
    - **accent**: Accent type (default: Standard)
    """
    # Verify user exists
    user = UserCRUD.get_user(db, user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User with id {user_id} not found"
        )
    
    db_voice_profile = VoiceProfileCRUD.create_voice_profile(db, user_id, voice_profile)
    return db_voice_profile


@router.get("/voice-profiles/{voice_profile_id}", response_model=VoiceProfileResponse)
async def get_voice_profile(voice_profile_id: int, db: Session = Depends(get_db)):
    """Get a specific voice profile."""
    voice_profile = VoiceProfileCRUD.get_voice_profile(db, voice_profile_id)
    if not voice_profile:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Voice profile with id {voice_profile_id} not found"
        )
    return voice_profile


@router.get("/users/{user_id}/voice-profiles", response_model=list[VoiceProfileResponse])
async def get_user_voice_profiles(
    user_id: int,
    skip: int = 0,
    limit: int = 10,
    db: Session = Depends(get_db)
):
    """Get all voice profiles for a user."""
    user = UserCRUD.get_user(db, user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User with id {user_id} not found"
        )
    
    voice_profiles = VoiceProfileCRUD.get_user_voice_profiles(db, user_id, skip, limit)
    return voice_profiles


@router.put("/voice-profiles/{voice_profile_id}", response_model=VoiceProfileResponse)
async def update_voice_profile(
    voice_profile_id: int,
    voice_profile_update: VoiceProfileUpdate,
    db: Session = Depends(get_db)
):
    """Update a voice profile."""
    db_voice_profile = VoiceProfileCRUD.update_voice_profile(db, voice_profile_id, voice_profile_update)
    if not db_voice_profile:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Voice profile with id {voice_profile_id} not found"
        )
    return db_voice_profile


@router.delete("/voice-profiles/{voice_profile_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_voice_profile(voice_profile_id: int, db: Session = Depends(get_db)):
    """Delete a voice profile."""
    success = VoiceProfileCRUD.delete_voice_profile(db, voice_profile_id)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Voice profile with id {voice_profile_id} not found"
        )
    return None


# ==================== VOICE SAMPLE ENDPOINTS ====================

@router.post("/voice-profiles/{voice_profile_id}/samples", response_model=VoiceSampleResponse, status_code=status.HTTP_201_CREATED)
async def upload_voice_sample(
    voice_profile_id: int,
    sample: VoiceSampleCreate,
    db: Session = Depends(get_db)
):
    """Upload a voice sample for a voice profile.
    
    This is STEP 2 in voice cloning:
    1. User uploads voice samples (audio recordings)
    2. Each sample must be at least 5 seconds
    3. More samples = better training
    
    - **file_path**: Path where the file is stored (e.g., "/uploads/mom_sample_1.wav")
    - **file_name**: Original filename
    - **duration_seconds**: Duration of the audio
    - **file_size_mb**: File size in MB
    """
    # Verify voice profile exists
    voice_profile = VoiceProfileCRUD.get_voice_profile(db, voice_profile_id)
    if not voice_profile:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Voice profile with id {voice_profile_id} not found"
        )
    
    # Validate audio duration (minimum 5 seconds)
    if sample.duration_seconds < 5:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Audio must be at least 5 seconds long"
        )
    
    db_sample = VoiceSampleCRUD.create_voice_sample(db, voice_profile_id, sample)
    return db_sample


@router.get("/voice-profiles/{voice_profile_id}/samples", response_model=list[VoiceSampleResponse])
async def get_voice_samples(
    voice_profile_id: int,
    skip: int = 0,
    limit: int = 50,
    db: Session = Depends(get_db)
):
    """Get all voice samples for a voice profile."""
    voice_profile = VoiceProfileCRUD.get_voice_profile(db, voice_profile_id)
    if not voice_profile:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Voice profile with id {voice_profile_id} not found"
        )
    
    samples = VoiceSampleCRUD.get_voice_samples(db, voice_profile_id, skip, limit)
    return samples


@router.delete("/samples/{sample_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_voice_sample(sample_id: int, db: Session = Depends(get_db)):
    """Delete a voice sample."""
    success = VoiceSampleCRUD.delete_voice_sample(db, sample_id)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Sample with id {sample_id} not found"
        )
    return None


# ==================== CONVERSATION ENDPOINTS ====================

@router.post(
    "/conversations",
    response_model=ConversationResponse,
    status_code=status.HTTP_201_CREATED
)
async def create_conversation(
    conversation: ConversationCreate | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Create a conversation for the authenticated user."""
    title = (
        conversation.title
        if conversation and conversation.title is not None
        else "New Chat"
    )

    return ConversationCRUD.create_conversation(
        db,
        current_user.user_id,
        title
    )


@router.get(
    "/conversations",
    response_model=list[ConversationResponse]
)
async def get_user_conversations(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Return the authenticated user's conversations."""
    return ConversationCRUD.get_user_conversations(
        db,
        current_user.user_id
    )


@router.get(
    "/conversations/{conversation_id}",
    response_model=ConversationResponse
)
async def get_conversation(
    conversation_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Return conversation metadata when owned by the current user."""
    conversation = ConversationCRUD.get_user_conversation(
        db,
        conversation_id,
        current_user.user_id
    )
    if not conversation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation not found."
        )
    return conversation


@router.patch(
    "/conversations/{conversation_id}",
    response_model=ConversationResponse
)
async def update_conversation(
    conversation_id: int,
    conversation_update: ConversationUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Rename a conversation owned by the authenticated user."""
    conversation = ConversationCRUD.get_user_conversation(
        db,
        conversation_id,
        current_user.user_id
    )
    if not conversation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation not found."
        )

    return ConversationCRUD.update_conversation_title(
        db,
        conversation,
        conversation_update.title
    )


@router.delete(
    "/conversations/{conversation_id}",
    status_code=status.HTTP_204_NO_CONTENT
)
async def delete_conversation(
    conversation_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Delete a conversation owned by the authenticated user."""
    conversation = ConversationCRUD.get_user_conversation(
        db,
        conversation_id,
        current_user.user_id
    )
    if not conversation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation not found."
        )

    ConversationCRUD.delete_conversation(db, conversation)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ==================== MESSAGE ENDPOINTS ====================

@router.post(
    "/conversations/{conversation_id}/messages",
    response_model=MessagePairResponse,
    status_code=status.HTTP_201_CREATED
)
async def create_message(
    conversation_id: int,
    message: MessageCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Generate and atomically store a user/assistant message pair."""
    conversation = ConversationCRUD.get_user_conversation(
        db,
        conversation_id,
        current_user.user_id
    )
    if not conversation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation not found."
        )

    try:
        assistant_content = await get_chat_service().generate_response(
            db,
            conversation,
            message.content
        )
    except AIServiceError as exc:
        logger.exception(
            "AI generation failed (category=%s, operation=non_streaming, "
            "conversation_id=%d).",
            getattr(exc, "code", "ai_service_error"),
            conversation_id,
        )
        raise _ai_http_exception(exc) from None

    user_message, assistant_message, conversation = MessageCRUD.create_message_pair(
        db,
        conversation,
        message.content,
        assistant_content
    )
    return MessagePairResponse(
        user_message=user_message,
        assistant_message=assistant_message,
        conversation=conversation
    )


@router.post(
    "/conversations/{conversation_id}/messages/stream",
)
async def create_message_stream(
    conversation_id: int,
    message: MessageCreate,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Stream Berry text and persist only a completed assistant response."""
    conversation = ConversationCRUD.get_user_conversation(
        db,
        conversation_id,
        current_user.user_id,
    )
    if not conversation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation not found.",
        )

    try:
        response_stream = get_chat_service().stream_response(
            db,
            conversation,
            message.content,
        )
    except AIServiceError as exc:
        logger.exception(
            "AI stream setup failed (category=%s, conversation_id=%d).",
            getattr(exc, "code", "ai_service_error"),
            conversation_id,
        )
        raise _ai_http_exception(exc) from None

    user_message, conversation = MessageCRUD.create_user_message(
        db,
        conversation,
        message.content,
    )
    start_payload = {
        "conversation_id": conversation_id,
        "user_message": MessageResponse.model_validate(
            user_message
        ).model_dump(mode="json"),
        "conversation": ConversationResponse.model_validate(
            conversation
        ).model_dump(mode="json"),
    }

    async def event_stream():
        chunks: list[str] = []

        try:
            yield _sse_event("start", start_payload)

            async for delta in response_stream:
                if await request.is_disconnected():
                    return

                chunks.append(delta)
                yield _sse_event("delta", {"text": delta})

            if await request.is_disconnected():
                return

            assistant_content = "".join(chunks).strip()
            if not assistant_content:
                raise AIInvalidResponseError(
                    "AI provider returned an empty response."
                )

            assistant_message, updated_conversation = (
                MessageCRUD.create_assistant_message(
                    db,
                    conversation,
                    assistant_content,
                )
            )
            yield _sse_event(
                "complete",
                {
                    "message": MessageResponse.model_validate(
                        assistant_message
                    ).model_dump(mode="json"),
                    "conversation": ConversationResponse.model_validate(
                        updated_conversation
                    ).model_dump(mode="json"),
                },
            )
        except asyncio.CancelledError:
            logger.info(
                "AI response stream cancelled (conversation_id=%d, "
                "delta_emitted=%s).",
                conversation_id,
                bool(chunks),
            )
            raise
        except AIServiceError as exc:
            logger.exception(
                "AI response stream failed (category=%s, "
                "conversation_id=%d, delta_emitted=%s).",
                getattr(exc, "code", "ai_service_error"),
                conversation_id,
                bool(chunks),
            )
            db.rollback()
            if not await request.is_disconnected():
                _, code, safe_message = _safe_ai_error(exc)
                yield _sse_event(
                    "error",
                    {
                        "code": code,
                        "message": safe_message,
                    },
                )
        except Exception:
            logger.exception(
                "Unexpected AI response stream failure "
                "(conversation_id=%d, delta_emitted=%s).",
                conversation_id,
                bool(chunks),
            )
            db.rollback()
            if not await request.is_disconnected():
                yield _sse_event(
                    "error",
                    {
                        "code": "stream_interrupted",
                        "message": "Berry's response was interrupted. Please try again.",
                    },
                )
        finally:
            close_stream = getattr(response_stream, "aclose", None)
            if close_stream is not None:
                await close_stream()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get(
    "/conversations/{conversation_id}/messages",
    response_model=list[MessageResponse]
)
async def get_messages(
    conversation_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Return complete message history for an owned conversation."""
    conversation = ConversationCRUD.get_user_conversation(
        db,
        conversation_id,
        current_user.user_id
    )
    if not conversation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation not found."
        )

    return MessageCRUD.get_conversation_messages(
        db,
        conversation_id
    )

from datetime import datetime

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.conversation import MessageRole


class ConversationCreate(BaseModel):
    title: str = Field(default="New chat", min_length=1, max_length=255)
    legacy_id: int | None = Field(default=None, ge=1)
    mode: Literal["rya"] = "rya"


class DailyPromptStart(BaseModel):
    legacy_id: int = Field(ge=1)
    prompt_id: int = Field(ge=1)


class LegacyConversationCreate(BaseModel):
    title: str = Field(default="New chat", min_length=1, max_length=255)
    legacy_id: int = Field(ge=1)
    mode: Literal["legacy"] = "legacy"


class ConversationRename(BaseModel):
    title: str = Field(min_length=1, max_length=80)

    @field_validator("title")
    @classmethod
    def normalize_title(cls, value: str) -> str:
        title = value.strip()
        if not title:
            raise ValueError("Conversation title must not be blank.")
        return title


class ConversationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    legacy_id: int | None
    title: str
    mode: str
    created_at: datetime
    updated_at: datetime


class MessageCreate(BaseModel):
    content: str = Field(min_length=1, max_length=12000)
    input_mode: Literal["text", "voice"] = "text"


class MessageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    conversation_id: int
    role: MessageRole
    content: str
    created_at: datetime
    web_sources: list["WebSourceResponse"] = Field(default_factory=list)


class WebSourceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    title: str
    domain: str
    url: str
    publication_date: str | None = None


class MessagePairResponse(BaseModel):
    user_message: MessageResponse
    rya_message: MessageResponse


class DailyPromptStartResponse(BaseModel):
    conversation: ConversationResponse
    rya_message: MessageResponse

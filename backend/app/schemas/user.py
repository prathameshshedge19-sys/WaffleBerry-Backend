"""Pydantic schemas for Voice Profiles."""

from pydantic import BaseModel, ConfigDict, Field, EmailStr, field_validator
from datetime import datetime
from typing import Literal, Optional

from app.models.user import MessageRole


# ==================== USER SCHEMAS ====================

class UserBase(BaseModel):
    """Base user schema."""
    full_name: str = Field(..., min_length=1, max_length=255)
    email: EmailStr


class UserCreate(UserBase):
    """Schema for creating a user."""
    password: str = Field(..., min_length=8, description="Password must be at least 8 characters")


class UserLogin(BaseModel):
    """Schema for authenticating a user."""
    email: EmailStr
    password: str

class VerifyEmailRequest(BaseModel):
    """Request body for email verification."""

    email: EmailStr
    otp: str

class ResendOTPRequest(BaseModel):
    """Request body for resending OTP."""

    email: EmailStr
    
class UserResponse(UserBase):
    """Schema for user response."""
    user_id: int
    created_at: datetime
    
    class Config:
        from_attributes = True


class SignupResponse(UserResponse):
    """Schema returned after creating or resuming signup."""

    verification_resent: bool = False


class LoginResponse(BaseModel):
    """Schema returned after successful authentication."""
    access_token: str
    token_type: Literal["bearer"]
    user: UserResponse


# ==================== VOICE PROFILE SCHEMAS ====================

class VoiceProfileBase(BaseModel):
    """Base voice profile schema."""
    voice_name: str = Field(..., min_length=1, max_length=255, description="Name of the voice (e.g., Mom, Dad)")
    relationship: str = Field(..., min_length=1, max_length=100, description="Relationship with voice owner")
    language: str = Field(default="English", max_length=50)
    accent: str = Field(default="Standard", max_length=100)


class VoiceProfileCreate(VoiceProfileBase):
    """Schema for creating a voice profile."""
    pass


class VoiceProfileUpdate(BaseModel):
    """Schema for updating a voice profile."""
    voice_name: Optional[str] = Field(None, min_length=1, max_length=255)
    relationship: Optional[str] = Field(None, min_length=1, max_length=100)
    language: Optional[str] = Field(None, max_length=50)
    accent: Optional[str] = Field(None, max_length=100)


class VoiceProfileResponse(VoiceProfileBase):
    """Schema for voice profile response."""
    voice_profile_id: int
    user_id: int
    training_status: str
    model_path: Optional[str]
    created_at: datetime
    updated_at: datetime
    
    class Config:
        from_attributes = True


# ==================== VOICE SAMPLE SCHEMAS ====================

class VoiceSampleBase(BaseModel):
    """Base voice sample schema."""
    file_name: str = Field(..., max_length=255)
    duration_seconds: int = Field(..., gt=0, description="Duration must be greater than 0")
    file_size_mb: int = Field(..., gt=0)


class VoiceSampleCreate(VoiceSampleBase):
    """Schema for creating a voice sample."""
    file_path: str


class VoiceSampleResponse(VoiceSampleBase):
    """Schema for voice sample response."""
    sample_id: int
    voice_profile_id: int
    file_path: str
    uploaded_at: datetime
    
    class Config:
        from_attributes = True


# ==================== CONVERSATION SCHEMAS ====================

class ConversationCreate(BaseModel):
    """Schema for creating a conversation."""
    title: Optional[str] = Field(None, max_length=255)

    @field_validator("title", mode="before")
    @classmethod
    def validate_title(cls, value):
        """Trim a supplied title and reject blank values."""
        if value is None:
            return None
        if not isinstance(value, str):
            return value

        title = value.strip()
        if not title:
            raise ValueError("Title must not be blank.")
        return title


class ConversationUpdate(BaseModel):
    """Schema for updating a conversation title."""
    title: str = Field(..., min_length=1, max_length=255)

    @field_validator("title", mode="before")
    @classmethod
    def validate_title(cls, value):
        """Trim the title and reject blank values."""
        if not isinstance(value, str):
            return value

        title = value.strip()
        if not title:
            raise ValueError("Title must not be blank.")
        return title


class ConversationResponse(BaseModel):
    """Schema for conversation metadata returned by the API."""
    conversation_id: int
    user_id: int
    title: str
    created_at: datetime
    updated_at: datetime
    
    model_config = ConfigDict(from_attributes=True)


# ==================== MESSAGE SCHEMAS ====================

class MessageCreate(BaseModel):
    """Schema for creating a user message."""
    content: str = Field(..., min_length=1)

    @field_validator("content", mode="before")
    @classmethod
    def validate_content(cls, value):
        """Trim message content and reject blank values."""
        if not isinstance(value, str):
            return value

        content = value.strip()
        if not content:
            raise ValueError("Message content must not be blank.")
        return content


class MessageResponse(BaseModel):
    """Schema for stored message metadata."""
    message_id: int
    conversation_id: int
    role: MessageRole
    content: str
    audio_path: Optional[str]
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class MessagePairResponse(BaseModel):
    """Schema containing the saved user and assistant messages."""
    user_message: MessageResponse
    assistant_message: MessageResponse
    conversation: ConversationResponse


# ==================== CONSENT SCHEMAS ====================

class ConsentCreate(BaseModel):
    """Schema for consent."""
    consent_given: bool
    consent_document_path: Optional[str] = None


class ConsentResponse(BaseModel):
    """Schema for consent response."""
    consent_id: int
    voice_profile_id: int
    consent_given: bool
    consent_date: datetime
    
    class Config:
        from_attributes = True


# ==================== USER SETTINGS SCHEMAS ====================

class UserSettingsCreate(BaseModel):
    """Schema for user settings."""
    theme: str = Field(default="light", pattern="^(light|dark)$")
    language: str = Field(default="English")
    speech_speed: str = Field(default="normal", pattern="^(slow|normal|fast)$")
    ai_personality: str = Field(default="friendly")


class UserSettingsResponse(UserSettingsCreate):
    """Schema for user settings response."""
    setting_id: int
    user_id: int
    created_at: datetime
    updated_at: datetime
    
    class Config:
        from_attributes = True

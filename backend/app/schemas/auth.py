from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class RegistrationStart(BaseModel):
    full_name: str = Field(min_length=1, max_length=255)
    email: EmailStr
    accepted_terms: bool


class AuthorizationRequest(BaseModel):
    email: EmailStr
    otp: str = Field(pattern=r"^\d{6}$")


class CompleteRegistration(BaseModel):
    verification_token: str = Field(min_length=20)
    password: str = Field(min_length=8, max_length=128)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)
    remember_me: bool = False


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    email: EmailStr
    reset_token: str = Field(min_length=20)
    password: str = Field(min_length=8, max_length=128)


class ResendRequest(BaseModel):
    email: EmailStr
    purpose: str = Field(pattern=r"^(registration|password_reset)$")


class GoogleLoginRequest(BaseModel):
    credential: str = Field(min_length=20)
    accepted_terms: bool = False


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    full_name: str
    email: EmailStr
    is_verified: bool
    created_at: datetime


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserResponse


class AuthorizationResponse(BaseModel):
    authorization: str


class MessageResponse(BaseModel):
    message: str

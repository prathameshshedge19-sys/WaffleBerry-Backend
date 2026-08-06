"""Pydantic schemas for registration API."""

from pydantic import BaseModel, EmailStr, Field


class RequestOTPRequest(BaseModel):
    """Request OTP for email verification."""
    full_name: str = Field(..., min_length=1, max_length=255)
    email: EmailStr


class RequestOTPResponse(BaseModel):
    """Response from OTP request."""
    status: str
    registration_id: str
    masked_email: str
    expires_in_seconds: int
    resend_available_in_seconds: int


class VerifyOTPRequest(BaseModel):
    """Verify OTP."""
    registration_id: str
    otp: str = Field(..., min_length=4, max_length=10)


class VerifyOTPResponse(BaseModel):
    """Response from OTP verification."""
    status: str
    registration_token: str
    expires_in_seconds: int


class ResendOTPRequest(BaseModel):
    """Request to resend OTP."""
    registration_id: str


class ResendOTPResponse(BaseModel):
    """Response from OTP resend."""
    status: str
    resend_available_in_seconds: int
    expires_in_seconds: int


class CompleteRegistrationRequest(BaseModel):
    """Complete registration with password."""
    registration_token: str
    password: str = Field(..., min_length=8)
    confirm_password: str


class UserResponseData(BaseModel):
    """User data in response."""
    user_id: int
    full_name: str
    email: str
    created_at: str | None


class CompleteRegistrationResponse(BaseModel):
    """Response from registration completion."""
    status: str
    user: UserResponseData
    access_token: str
    token_type: str

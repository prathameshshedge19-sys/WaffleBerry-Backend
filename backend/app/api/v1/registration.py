"""API routes for email-verified registration."""

import logging
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.schemas.registration import (
    RequestOTPRequest,
    RequestOTPResponse,
    VerifyOTPRequest,
    VerifyOTPResponse,
    ResendOTPRequest,
    ResendOTPResponse,
    CompleteRegistrationRequest,
    CompleteRegistrationResponse,
)
from app.services.registration_service import RegistrationService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/registration", tags=["registration"])


@router.post("/email/request-otp", response_model=RequestOTPResponse)
async def request_otp(
    request_data: RequestOTPRequest,
    db: Session = Depends(get_db),
):
    """
    Request OTP for email verification.
    
    Initiates the registration flow by sending an OTP to the provided email.
    """
    try:
        service = RegistrationService(db)
        result = await service.request_otp(
            full_name=request_data.full_name,
            email=request_data.email,
        )
        return result
    except ValueError as e:
        logger.warning(f"Registration OTP request error: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except Exception as e:
        logger.error(f"Unexpected error during OTP request: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to process registration request",
        )


@router.post("/email/verify-otp", response_model=VerifyOTPResponse)
def verify_otp(
    request_data: VerifyOTPRequest,
    db: Session = Depends(get_db),
):
    """
    Verify OTP and mark email as verified.
    
    Returns a registration token for the password creation step.
    """
    try:
        service = RegistrationService(db)
        result = service.verify_otp(
            registration_id=request_data.registration_id,
            otp=request_data.otp,
        )
        return result
    except ValueError as e:
        logger.warning(f"OTP verification error: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except Exception as e:
        logger.error(f"Unexpected error during OTP verification: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to verify OTP",
        )


@router.post("/email/resend-otp", response_model=ResendOTPResponse)
async def resend_otp(
    request_data: ResendOTPRequest,
    db: Session = Depends(get_db),
):
    """
    Resend OTP with rate limiting.
    
    Invalidates the previous OTP and sends a new one.
    Subject to cooldown and hourly rate limits.
    """
    try:
        service = RegistrationService(db)
        result = await service.resend_otp(registration_id=request_data.registration_id)
        return result
    except ValueError as e:
        logger.warning(f"OTP resend error: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except Exception as e:
        logger.error(f"Unexpected error during OTP resend: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to resend OTP",
        )


@router.post("/complete", response_model=CompleteRegistrationResponse)
def complete_registration(
    request_data: CompleteRegistrationRequest,
    db: Session = Depends(get_db),
):
    """
    Complete registration by creating user account.
    
    Requires a valid registration token and matching passwords.
    Returns access token for immediate login.
    """
    # Validate passwords match
    if request_data.password != request_data.confirm_password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Passwords do not match",
        )

    try:
        service = RegistrationService(db)
        result = service.complete_registration(
            registration_token=request_data.registration_token,
            password=request_data.password,
        )
        return result
    except ValueError as e:
        logger.warning(f"Registration completion error: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except Exception as e:
        logger.error(f"Unexpected error during registration completion: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to complete registration",
        )

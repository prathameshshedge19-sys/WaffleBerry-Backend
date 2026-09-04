import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.config import get_settings
from app.database import get_db
from app.models.auth_challenge import AuthChallenge
from app.models.user import User
from app.schemas.auth import (
    AuthorizationRequest, AuthorizationResponse, CompleteRegistration,
    ForgotPasswordRequest, GoogleLoginRequest, LoginRequest, LoginResponse,
    MessageResponse, RegistrationStart, ResendRequest, ResetPasswordRequest,
    UserResponse,
)
from app.services.challenges import consume_authorization, create_challenge, verify_challenge
from app.services.email import EmailDeliveryError, email_sender
from app.services.google_identity import (
    GoogleIdentityConfigurationError, GoogleIdentityError, verify_google_credential,
)
from app.services.security import (
    TokenValidationError, create_access_token, create_refresh_token, decode_token,
    hash_password, normalize_email, refresh_token_matches, verify_password,
)


router = APIRouter(prefix="/auth", tags=["authentication"])
REFRESH_COOKIE = "legarya_refresh"


def _set_session(response: Response, user: User, *, remember_me: bool = False) -> LoginResponse:
    settings = get_settings()
    max_age = settings.remembered_refresh_token_expire_days * 86400 if remember_me else None
    expires = datetime.now(timezone.utc) + timedelta(seconds=max_age) if max_age else None
    response.set_cookie(
        REFRESH_COOKIE,
        create_refresh_token(user.id, user.password_hash, remember_me=remember_me),
        httponly=True,
        secure=not settings.legarya_debug,
        samesite="lax",
        max_age=max_age,
        expires=expires,
        path="/api/v1/auth",
    )
    return LoginResponse(access_token=create_access_token(user.id), user=user)


@router.post("/register", response_model=MessageResponse, status_code=status.HTTP_202_ACCEPTED)
def register(payload: RegistrationStart, db: Session = Depends(get_db)):
    email = normalize_email(str(payload.email))
    if not payload.accepted_terms:
        raise HTTPException(status_code=400, detail="Terms must be accepted.")
    if db.scalar(select(User).where(User.email == email)):
        raise HTTPException(status_code=409, detail="Email already registered.")
    code = create_challenge(db, email=email, purpose="registration", full_name=payload.full_name.strip())
    try:
        email_sender.send_code(recipient=email, code=code, purpose="registration")
    except EmailDeliveryError:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "email_delivery_failed",
                "message": "Unable to send the verification email. Check the local Legarya backend log.",
            },
        ) from None
    return {"message": "Verification code sent."}


@router.post("/verify-email", response_model=AuthorizationResponse)
def verify_email(payload: AuthorizationRequest, db: Session = Depends(get_db)):
    authorization = verify_challenge(db, email=str(payload.email), purpose="registration", otp=payload.otp)
    if not authorization:
        raise HTTPException(status_code=400, detail="Invalid or expired verification code.")
    return {"authorization": authorization}


@router.post("/complete-registration", response_model=LoginResponse, status_code=status.HTTP_201_CREATED)
def complete_registration(payload: CompleteRegistration, response: Response, db: Session = Depends(get_db)):
    challenge = consume_authorization(db, authorization=payload.verification_token, purpose="registration")
    if not challenge or not challenge.full_name:
        db.rollback()
        raise HTTPException(status_code=400, detail="Invalid or expired verification authorization.")
    user = User(full_name=challenge.full_name, email=challenge.email, password_hash=hash_password(payload.password), is_verified=True)
    db.add(user)
    try:
        db.commit()
        db.refresh(user)
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Email already registered.") from None
    return _set_session(response, user)


@router.post("/login", response_model=LoginResponse)
def login(payload: LoginRequest, response: Response, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == normalize_email(str(payload.email))))
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password.")
    if not user.is_verified:
        raise HTTPException(status_code=403, detail="Email address is not verified.")
    return _set_session(response, user, remember_me=payload.remember_me)


@router.post("/refresh", response_model=LoginResponse)
def refresh(request: Request, response: Response, db: Session = Depends(get_db)):
    token = request.cookies.get(REFRESH_COOKIE)
    try:
        payload = decode_token(token or "", "refresh")
    except TokenValidationError:
        raise HTTPException(status_code=401, detail="Invalid or expired session.") from None
    user = db.get(User, payload["user_id"])
    if not user or not refresh_token_matches(user.id, user.password_hash, payload.get("fingerprint", "")):
        raise HTTPException(status_code=401, detail="Invalid or expired session.")
    return _set_session(response, user, remember_me=payload.get("remember_me") is True)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(response: Response):
    settings = get_settings()
    response.delete_cookie(
        REFRESH_COOKIE,
        path="/api/v1/auth",
        secure=not settings.legarya_debug,
        httponly=True,
        samesite="lax",
    )


@router.get("/me", response_model=UserResponse)
def me(user: User = Depends(get_current_user)):
    return user


@router.post("/forgot-password", response_model=MessageResponse)
def forgot_password(payload: ForgotPasswordRequest, db: Session = Depends(get_db)):
    email = normalize_email(str(payload.email))
    user = db.scalar(select(User).where(User.email == email))
    if user:
        code = create_challenge(db, email=email, purpose="password_reset")
        try:
            email_sender.send_code(recipient=email, code=code, purpose="password_reset")
        except EmailDeliveryError:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "email_delivery_failed",
                    "message": "Unable to send the password reset email. Check the local Legarya backend log.",
                },
            ) from None
    return {"message": "If that account exists, a reset code has been sent."}


@router.post("/verify-reset-otp", response_model=AuthorizationResponse)
def verify_reset_otp(payload: AuthorizationRequest, db: Session = Depends(get_db)):
    authorization = verify_challenge(db, email=str(payload.email), purpose="password_reset", otp=payload.otp)
    if not authorization:
        raise HTTPException(status_code=400, detail="Invalid or expired reset code.")
    return {"authorization": authorization}


@router.post("/reset-password", response_model=MessageResponse)
def reset_password(payload: ResetPasswordRequest, db: Session = Depends(get_db)):
    challenge = consume_authorization(db, authorization=payload.reset_token, purpose="password_reset", email=str(payload.email))
    if not challenge:
        db.rollback()
        raise HTTPException(status_code=400, detail="Invalid or expired reset authorization.")
    user = db.scalar(select(User).where(User.email == challenge.email))
    if not user:
        db.rollback()
        raise HTTPException(status_code=400, detail="Invalid or expired reset authorization.")
    user.password_hash = hash_password(payload.password)
    db.commit()
    return {"message": "Password reset successfully."}


@router.post("/resend-otp", response_model=MessageResponse)
def resend_otp(payload: ResendRequest, db: Session = Depends(get_db)):
    email = normalize_email(str(payload.email))
    user = db.scalar(select(User).where(User.email == email))
    if payload.purpose == "registration" and user:
        raise HTTPException(status_code=409, detail="Email already registered.")
    if payload.purpose == "password_reset" and not user:
        return {"message": "If that account exists, a reset code has been sent."}
    full_name = None
    if payload.purpose == "registration":
        previous = db.scalar(
            select(AuthChallenge)
            .where(
                AuthChallenge.email == email,
                AuthChallenge.purpose == "registration",
            )
            .order_by(AuthChallenge.id.desc())
        )
        if not previous or not previous.full_name:
            raise HTTPException(status_code=400, detail="Start registration first.")
        full_name = previous.full_name
    code = create_challenge(db, email=email, purpose=payload.purpose, full_name=full_name)
    try:
        email_sender.send_code(recipient=email, code=code, purpose=payload.purpose)
    except EmailDeliveryError:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "email_delivery_failed",
                "message": "Unable to resend the authentication email. Check the local Legarya backend log.",
            },
        ) from None
    return {"message": "Verification code sent."}


@router.post("/google", response_model=LoginResponse)
def google_login(payload: GoogleLoginRequest, response: Response, db: Session = Depends(get_db)):
    try:
        identity = verify_google_credential(payload.credential)
    except GoogleIdentityConfigurationError:
        raise HTTPException(
            status_code=503,
            detail={"code": "google_auth_unavailable", "message": "Google Sign-In is not configured."},
        ) from None
    except GoogleIdentityError:
        raise HTTPException(status_code=401, detail="Invalid Google credential.") from None
    user = db.scalar(select(User).where(User.google_sub == identity.sub))
    email_user = db.scalar(select(User).where(User.email == identity.email))
    if user and email_user and user.id != email_user.id:
        raise HTTPException(
            status_code=409,
            detail={"code": "google_identity_conflict", "message": "Google account conflict."},
        )
    user = user or email_user
    if user:
        if user.google_sub not in (None, identity.sub):
            raise HTTPException(
                status_code=409,
                detail={"code": "google_identity_conflict", "message": "Google account conflict."},
            )
        user.google_sub = identity.sub
        user.is_verified = True
    else:
        if not payload.accepted_terms:
            raise HTTPException(
                status_code=403,
                detail={"code": "terms_required", "message": "Terms must be accepted."},
            )
        user = User(full_name=identity.name, email=identity.email, google_sub=identity.sub, password_hash=hash_password(secrets.token_urlsafe(48)), is_verified=True)
        db.add(user)
    try:
        db.commit()
        db.refresh(user)
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail={"code": "google_identity_conflict", "message": "Google account conflict."},
        ) from None
    return _set_session(response, user)

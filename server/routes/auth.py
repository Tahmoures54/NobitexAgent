# server/routes/auth.py
"""Passwordless authenticator-app registration and login routes."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from server.auth import (
    build_totp_setup,
    create_access_token,
    encrypt_totp_secret,
    generate_totp_secret,
    get_current_user,
    verify_totp,
)
from server.database import get_db
from server.models import User
from server.schemas import LoginReq, RegisterReq, TotpSetupOut, TokenResp, UserOut, VerifyTotpReq
from server.security import client_ip, client_ua, limiter
from server.services import audit

router = APIRouter(prefix="/auth", tags=["auth"])


def _response(user: User) -> TokenResp:
    return TokenResp(access_token=create_access_token(user.id), user=UserOut.from_user(user))


def _setup(user: User, secret: str) -> TotpSetupOut:
    uri, qr = build_totp_setup(secret, user.email)
    return TotpSetupOut(secret=secret, otpauth_uri=uri, qr_code_data_uri=qr)


@router.post("/register", response_model=TotpSetupOut, summary="Register and configure an authenticator app")
@limiter.limit("5/hour")
def register(request: Request, req: RegisterReq, db: Session = Depends(get_db)) -> TotpSetupOut:
    email = str(req.email).strip().lower()
    user = db.query(User).filter(User.email == email).first()

    if user is not None and user.totp_enabled:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="An account with this email already exists.")

    secret = generate_totp_secret()

    if user is None:
        user = User(
            email=email,
            auth_provider="totp",
            auth_subject=f"totp:{email}",
            full_name=req.full_name,
            phone_number=req.phone_number,
            totp_secret_encrypted=encrypt_totp_secret(secret),
            totp_enabled=False,
            password_hash=None,
            plan="free",
            is_active=True,
        )
        db.add(user)
    else:
        user.auth_provider = "totp"
        user.auth_subject = f"totp:{email}"
        user.full_name = req.full_name
        user.phone_number = req.phone_number
        user.totp_secret_encrypted = encrypt_totp_secret(secret)
        user.totp_enabled = False
        user.password_hash = None

    db.commit()
    db.refresh(user)

    audit.log_event(
        db, user_id=user.id, event=audit.EV_REGISTER,
        ip=client_ip(request), user_agent=client_ua(request),
        details={"method": "totp"},
    )
    return _setup(user, secret)


@router.post("/setup/verify", response_model=TokenResp, summary="Verify the authenticator code and activate the account")
@limiter.limit("10/minute")
def verify_setup(
    request: Request,
    req: LoginReq,
    db: Session = Depends(get_db),
) -> TokenResp:
    email = str(req.email).strip().lower()
    user = db.query(User).filter(User.email == email).first()
    if user is None or not user.totp_secret_encrypted:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Account setup was not found.")

    from server.auth import decrypt_totp_secret
    try:
        secret = decrypt_totp_secret(user.totp_secret_encrypted)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Authenticator setup is invalid.") from exc

    if not verify_totp(secret, req.code):
        audit.log_event(
            db, user_id=user.id, event=audit.EV_LOGIN_FAIL,
            ip=client_ip(request), user_agent=client_ua(request),
            details={"method": "totp", "stage": "setup"},
        )
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid authenticator code.")

    user.totp_enabled = True
    user.last_login_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(user)

    audit.log_event(
        db, user_id=user.id, event=audit.EV_LOGIN_OK,
        ip=client_ip(request), user_agent=client_ua(request),
        details={"method": "totp", "stage": "activation"},
    )
    return _response(user)


@router.post("/login", response_model=TokenResp, summary="Sign in with authenticator code")
@limiter.limit("10/minute")
def login(
    request: Request,
    req: LoginReq,
    db: Session = Depends(get_db),
) -> TokenResp:
    email = str(req.email).strip().lower()
    user = db.query(User).filter(User.email == email).first()

    if user is None or not user.is_active or not user.totp_enabled or not user.totp_secret_encrypted:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or authenticator code.")

    from server.auth import decrypt_totp_secret
    try:
        secret = decrypt_totp_secret(user.totp_secret_encrypted)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authenticator configuration is invalid.")

    if not verify_totp(secret, req.code):
        audit.log_event(
            db, user_id=user.id, event=audit.EV_LOGIN_FAIL,
            ip=client_ip(request), user_agent=client_ua(request),
            details={"method": "totp"},
        )
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or authenticator code.")

    user.last_login_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(user)

    audit.log_event(
        db, user_id=user.id, event=audit.EV_LOGIN_OK,
        ip=client_ip(request), user_agent=client_ua(request),
        details={"method": "totp"},
    )
    return _response(user)


@router.get("/me", response_model=UserOut, summary="Current authenticated user")
def me(user: User = Depends(get_current_user)) -> UserOut:
    return UserOut.from_user(user)


@router.post("/logout", summary="Client-side logout")
def logout(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    audit.log_event(
        db, user_id=user.id, event=audit.EV_LOGOUT,
        ip=client_ip(request), user_agent=client_ua(request),
    )
    return {"status": "ok", "message": "Session cleared client-side."}


__all__ = ["router"]

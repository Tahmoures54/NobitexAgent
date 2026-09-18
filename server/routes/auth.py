# server/routes/auth.py
"""
Authentication routes.

    POST /auth/register  — create account, returns JWT
    POST /auth/login     — password login, returns JWT
    POST /auth/token     — OAuth2 form login (for Swagger)
    GET  /auth/me        — current user
    POST /auth/logout    — client-side hint (JWT is stateless)

Rate limits (per IP):
    register : 5 / minute
    login    : 10 / minute
    token    : 10 / minute
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from server.auth import (
    create_access_token,
    get_current_user,
    hash_password,
    verify_password,
)
from server.config import settings
from server.database import get_db
from server.models import User
from server.schemas import LoginReq, RegisterReq, TokenResp, UserOut
from server.security import client_ip, client_ua, limiter
from server.services import audit

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


# ══════════════════════════════════════════════════════════
# Register
# ══════════════════════════════════════════════════════════
@router.post(
    "/register",
    response_model=TokenResp,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new account",
)
@limiter.limit("5/minute")
def register(
    request: Request,
    req: RegisterReq,
    db: Session = Depends(get_db),
) -> TokenResp:
    email = req.email.strip().lower()

    existing = db.query(User).filter(User.email == email).first()
    if existing is not None:
        audit.log_event(
            db, event=audit.EV_REGISTER, ip=client_ip(request),
            user_agent=client_ua(request),
            details={"email": email, "result": "duplicate"},
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already registered.",
        )

    user = User(
        email=email,
        password_hash=hash_password(req.password),
        plan="free",
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    token = create_access_token(user.id)

    audit.log_event(
        db, user_id=user.id, event=audit.EV_REGISTER,
        ip=client_ip(request), user_agent=client_ua(request),
        details={"email": email},
    )

    logger.info("User registered: %s (id=%d)", email, user.id)

    return TokenResp(access_token=token, user=UserOut.model_validate(user))


# ══════════════════════════════════════════════════════════
# Login (JSON)
# ══════════════════════════════════════════════════════════
@router.post("/login", response_model=TokenResp, summary="Password login (JSON)")
@limiter.limit("10/minute")
def login(
    request: Request,
    req: LoginReq,
    db: Session = Depends(get_db),
) -> TokenResp:
    email = req.email.strip().lower()
    user = db.query(User).filter(User.email == email).first()

    if user is None or not verify_password(req.password, user.password_hash):
        audit.log_event(
            db, user_id=user.id if user else None,
            event=audit.EV_LOGIN_FAIL,
            ip=client_ip(request), user_agent=client_ua(request),
            details={"email": email},
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password.",
        )

    if not user.is_active:
        audit.log_event(
            db, user_id=user.id, event=audit.EV_LOGIN_FAIL,
            ip=client_ip(request), user_agent=client_ua(request),
            details={"email": email, "reason": "inactive"},
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is disabled.",
        )

    user.last_login_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(user)

    token = create_access_token(user.id)

    audit.log_event(
        db, user_id=user.id, event=audit.EV_LOGIN_OK,
        ip=client_ip(request), user_agent=client_ua(request),
    )

    logger.info("Login OK: %s (id=%d)", email, user.id)

    return TokenResp(access_token=token, user=UserOut.model_validate(user))


# ══════════════════════════════════════════════════════════
# OAuth2 form login (for Swagger UI)
# ══════════════════════════════════════════════════════════
@router.post(
    "/token",
    response_model=TokenResp,
    include_in_schema=False,
)
@limiter.limit("10/minute")
def token(
    request: Request,
    form: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
) -> TokenResp:
    email = (form.username or "").strip().lower()
    user = db.query(User).filter(User.email == email).first()

    if user is None or not verify_password(form.password, user.password_hash):
        audit.log_event(
            db, user_id=user.id if user else None,
            event=audit.EV_LOGIN_FAIL,
            ip=client_ip(request), user_agent=client_ua(request),
            details={"email": email, "via": "oauth2_form"},
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials.",
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is disabled.",
        )

    user.last_login_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(user)

    token = create_access_token(user.id)

    audit.log_event(
        db, user_id=user.id, event=audit.EV_LOGIN_OK,
        ip=client_ip(request), user_agent=client_ua(request),
        details={"via": "oauth2_form"},
    )

    return TokenResp(access_token=token, user=UserOut.model_validate(user))


# ══════════════════════════════════════════════════════════
# Current user
# ══════════════════════════════════════════════════════════
@router.get("/me", response_model=UserOut, summary="Current authenticated user")
def me(user: User = Depends(get_current_user)) -> UserOut:
    return UserOut.model_validate(user)


# ══════════════════════════════════════════════════════════
# Logout (client-side — JWT is stateless)
# ══════════════════════════════════════════════════════════
@router.post("/logout", summary="Hint: drop the token client-side")
def logout(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    audit.log_event(
        db, user_id=user.id, event=audit.EV_LOGOUT,
        ip=client_ip(request), user_agent=client_ua(request),
    )
    return {"status": "ok", "message": "Token discarded client-side."}


__all__ = ["router"]
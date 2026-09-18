# server/routes/auth.py
"""Authentication routes using Google Identity Services and Microsoft identity only."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from server.auth import create_access_token, get_current_user, verify_external_token
from server.config import settings
from server.database import get_db
from server.models import User
from server.schemas import AuthConfigOut, ProfileUpdateReq, ProviderTokenReq, TokenResp, UserOut
from server.security import client_ip, client_ua, limiter
from server.services import audit

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])


def _response(user: User) -> TokenResp:
    return TokenResp(access_token=create_access_token(user.id), user=UserOut.from_user(user))


@router.get("/config", response_model=AuthConfigOut, summary="Public authentication configuration")
def auth_config() -> AuthConfigOut:
    return AuthConfigOut(
        google_client_id=settings.google_client_id,
        microsoft_client_id=settings.microsoft_client_id,
        microsoft_authority=settings.microsoft_authority,
    )


@router.post("/provider", response_model=TokenResp, summary="Sign in with Google or Microsoft")
@limiter.limit("10/minute")
def provider_login(request: Request, req: ProviderTokenReq, db: Session = Depends(get_db)) -> TokenResp:
    try:
        identity = verify_external_token(req.provider, req.id_token)
    except ValueError as exc:
        audit.log_event(
            db, event=audit.EV_LOGIN_FAIL, ip=client_ip(request),
            user_agent=client_ua(request),
            details={"provider": req.provider, "reason": str(exc)},
        )
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    except Exception:
        logger.exception("External identity verification failed")
        audit.log_event(
            db, event=audit.EV_LOGIN_FAIL, ip=client_ip(request),
            user_agent=client_ua(request),
            details={"provider": req.provider, "reason": "token verification error"},
        )
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Identity verification failed.")

    provider = identity["provider"]
    subject = identity["subject"]
    email = identity["email"]
    name = (identity.get("name") or "").strip() or None

    user = db.query(User).filter(
        User.auth_provider == provider,
        User.auth_subject == subject,
    ).first()
    is_new_user = user is None

    if user is None:
        existing = db.query(User).filter(User.email == email).first()
        if existing is not None:
            if existing.auth_provider not in {"legacy", provider}:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="This email is already linked to another sign-in provider. Sign in with that provider first.",
                )
            user = existing
            user.auth_provider = provider
            user.auth_subject = subject
            user.password_hash = None
        else:
            user = User(
                email=email,
                auth_provider=provider,
                auth_subject=subject,
                full_name=name,
                password_hash=None,
                plan="free",
                is_active=True,
            )
            db.add(user)

    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account is disabled.")

    if name and not user.full_name:
        user.full_name = name

    user.last_login_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(user)

    if is_new_user:
        audit.log_event(
            db, user_id=user.id, event=audit.EV_REGISTER,
            ip=client_ip(request), user_agent=client_ua(request),
            details={"provider": provider},
        )
    audit.log_event(
        db, user_id=user.id, event=audit.EV_LOGIN_OK,
        ip=client_ip(request), user_agent=client_ua(request),
        details={"provider": provider},
    )
    return _response(user)


@router.patch("/profile", response_model=UserOut, summary="Complete required profile")
@limiter.limit("10/minute")
def update_profile(
    request: Request,
    req: ProfileUpdateReq,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> UserOut:
    user.full_name = req.full_name
    user.phone_number = req.phone_number
    db.commit()
    db.refresh(user)
    audit.log_event(
        db, user_id=user.id, event="profile_updated",
        ip=client_ip(request), user_agent=client_ua(request),
    )
    return UserOut.from_user(user)


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

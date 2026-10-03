# ============================================================
# routers/auth.py — Authentication Routes
# Login, Logout, Password Change
# NO OTP for BHW registration — removed per requirements
# Audit log: LOGIN and LOGOUT only
# ============================================================

import os
import math
import time
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status, Request
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from datetime import datetime, timedelta

from database import get_db
from models.models import User, AuditLog
from middleware.auth import (
    get_current_user, log_audit, handle_failed_login,
    reset_failed_attempts, get_client_info,
    security, LOCKOUT_DURATION_MIN
)
from utils.security import (
    verify_password, hash_password,
    create_access_token, decode_access_token,
    validate_password_strength, ACCESS_TOKEN_EXPIRE_MIN
)

router = APIRouter(prefix="/api/auth", tags=["Authentication"])

# Absolute na maximum na haba ng isang session kahit paulit-ulit ang silent refresh.
# Default: 12 oras (isang shift). Pagkatapos nito, kailangan nang mag-login ulit.
MAX_SESSION_HOURS = int(os.getenv("MAX_SESSION_HOURS", "12"))


def _issue_token(user: User, auth_time: Optional[int] = None):
    """
    Gumawa ng access token para sa user.
    auth_time = kailan TALAGANG na-verify ang password (login / unlock).
    Hindi ito nagbabago kapag nag-refresh, kaya may hangganan ang bawat session.
    Returns (token, expires_in_seconds).
    """
    token = create_access_token({
        "user_id":   user.user_id,
        "email":     user.email,
        "role":      user.role,
        "auth_time": auth_time or int(time.time())
    })
    return token, ACCESS_TOKEN_EXPIRE_MIN * 60


def _lockout_error(seconds: int, detail: str) -> HTTPException:
    """423 na may Retry-After header para eksakto ang countdown sa frontend."""
    return HTTPException(
        status_code=423,
        detail=detail,
        headers={"Retry-After": str(max(1, int(seconds)))}
    )


@router.post("/login")
async def login(request: Request, db: Session = Depends(get_db)):
    """
    Login endpoint para sa lahat ng users.
    May brute-force protection at audit logging.
    """
    ip_address, user_agent = get_client_info(request)
    body = await request.json()
    email    = body.get("email", "").strip().lower()
    password = body.get("password", "")

    if not email or not password:
        raise HTTPException(status_code=400, detail="Email and password are required.")

    # Hanapin ang user
    user = db.query(User).filter(User.email == email).first()

    if not user:
        raise HTTPException(status_code=401, detail="Invalid email or password.")

    # Check kung naka-lock
    if user.status == "locked":
        if user.locked_until and datetime.utcnow() > user.locked_until:
            reset_failed_attempts(db, user)
        else:
            if user.locked_until:
                secs = max(1, math.ceil((user.locked_until - datetime.utcnow()).total_seconds()))
                mins = math.ceil(secs / 60)
                raise _lockout_error(secs, f"Account is locked. Try again in {mins} minute(s).")
            raise HTTPException(status_code=423, detail="Account is locked. Contact administrator.")

    if user.status == "inactive":
        raise HTTPException(status_code=403, detail="Account is deactivated. Contact administrator.")

    # Verify password
    if not verify_password(password, user.password_hash):
        result = handle_failed_login(db, user)
        if result["locked"]:
            raise _lockout_error(LOCKOUT_DURATION_MIN * 60, result["message"])
        raise HTTPException(status_code=401, detail=result["message"])

    # Successful login
    reset_failed_attempts(db, user)
    user.last_login = datetime.utcnow()
    db.commit()

    # Create JWT token
    access_token, expires_in = _issue_token(user)

    # Log LOGIN event
    log_audit(
        db, user.user_id,
        f"LOGIN - {user.role.upper()}",
        ip_address=ip_address,
        user_agent=user_agent
    )

    return {
        "access_token":  access_token,
        "token_type":    "bearer",
        "user_id":       user.user_id,
        "name":          user.name,
        "role":          user.role,
        "email":         user.email,
        "position":      user.position,
        "is_first_login": getattr(user, 'is_first_login', False),
        "expires_in":    expires_in
    }


@router.post("/logout")
async def logout(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Logout — i-log ang event at i-invalidate ang session."""
    ip_address, user_agent = get_client_info(request)
    log_audit(db, current_user.user_id, "LOGOUT",
              ip_address=ip_address, user_agent=user_agent)
    return {"message": "Logged out successfully."}


@router.post("/refresh")
async def refresh_token(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    current_user: User = Depends(get_current_user)
):
    """
    Silent token refresh para sa AKTIBONG user (tinatawag ng session.js).
    Hindi nito nire-reset ang auth_time, kaya may absolute limit ang session
    (MAX_SESSION_HOURS) kahit paulit-ulit ang refresh.
    """
    payload   = decode_access_token(credentials.credentials) or {}
    auth_time = int(payload.get("auth_time") or payload.get("iat") or 0)

    if auth_time <= 0 or time.time() - auth_time > MAX_SESSION_HOURS * 3600:
        raise HTTPException(
            status_code=401,
            detail="Session limit reached. Please login again.",
            headers={"WWW-Authenticate": "Bearer"}
        )

    token, expires_in = _issue_token(current_user, auth_time=auth_time)
    return {"access_token": token, "token_type": "bearer", "expires_in": expires_in}


@router.post("/verify-password")
async def verify_session_password(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Pang-unlock ng screen lock. Vine-verify ang password ng KASALUKUYANG naka-login
    na user — hindi na bagong LOGIN (walang ingay sa audit log ng LOGIN).
    Bilang pa rin ang maling password sa lockout counter (brute-force protection).
    Kapag tama: nare-reset ang counter at nagbibigay ng bagong token.
    """
    body     = await request.json()
    password = body.get("password", "") if isinstance(body, dict) else ""
    if not password:
        raise HTTPException(status_code=400, detail="Password is required.")

    ip_address, user_agent = get_client_info(request)

    if not verify_password(password, current_user.password_hash):
        result = handle_failed_login(db, current_user)
        if result["locked"]:
            raise _lockout_error(LOCKOUT_DURATION_MIN * 60, result["message"])
        raise HTTPException(status_code=400, detail=result["message"])

    reset_failed_attempts(db, current_user)
    log_audit(db, current_user.user_id, "SESSION UNLOCK",
              ip_address=ip_address, user_agent=user_agent)

    token, expires_in = _issue_token(current_user)
    return {"access_token": token, "token_type": "bearer", "expires_in": expires_in}


@router.post("/change-password")
async def change_password(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Palitan ang password ng kasalukuyang user."""
    body = await request.json()
    current_pw = body.get("current_password", "")
    new_pw     = body.get("new_password", "")
    confirm_pw = body.get("confirm_password", "")

    if not verify_password(current_pw, current_user.password_hash):
        raise HTTPException(status_code=400, detail="Current password is incorrect.")
    if new_pw != confirm_pw:
        raise HTTPException(status_code=400, detail="Passwords do not match.")

    is_strong, msg = validate_password_strength(new_pw)
    if not is_strong:
        raise HTTPException(status_code=400, detail=msg)

    current_user.password_hash = hash_password(new_pw)
    db.commit()
    return {"message": "Password changed successfully."}


@router.get("/me")
async def get_me(current_user: User = Depends(get_current_user)):
    """Kunin ang complete profile info ng naka-login na user."""
    from database import get_barangay_name
    return {
        "user_id":        current_user.user_id,
        "name":           current_user.name,
        "email":          current_user.email,
        "role":           current_user.role,
        "position":       current_user.position,
        "status":         current_user.status,
        "is_first_login": getattr(current_user, 'is_first_login', False),
        "barangay_name":  get_barangay_name(),
        "last_login":     str(current_user.last_login) if current_user.last_login else None,
        "created_at":     str(current_user.created_at)
    }


@router.put("/me")
async def update_profile(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    I-update ang profile ng naka-login na user.
    Pwedeng baguhin ang: name at position lang.
    Hindi pwedeng baguhin ang email at role dito.
    """
    body = await request.json()

    if "name" in body and body["name"].strip():
        current_user.name = body["name"].strip()
    if "position" in body:
        current_user.position = body["position"].strip() or None

    db.commit()
    db.refresh(current_user)

    return {
        "message":  "Profile updated successfully.",
        "user_id":  current_user.user_id,
        "name":     current_user.name,
        "position": current_user.position
    }


@router.post("/change-password-first-login")
async def change_password_first_login(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Special endpoint para sa first-time login password change.
    Pagkatapos mag-change, itatakda ang is_first_login = False
    para hindi na ulit ipapakita ang forced change password screen.
    """
    body = await request.json()
    current_pw = body.get("current_password", "")
    new_pw     = body.get("new_password", "")
    confirm_pw = body.get("confirm_password", "")

    if not verify_password(current_pw, current_user.password_hash):
        raise HTTPException(status_code=400, detail="Current password is incorrect.")

    if new_pw != confirm_pw:
        raise HTTPException(status_code=400, detail="New passwords do not match.")

    if current_pw == new_pw:
        raise HTTPException(status_code=400, detail="New password must be different from the current password.")

    is_strong, msg = validate_password_strength(new_pw)
    if not is_strong:
        raise HTTPException(status_code=400, detail=msg)

    # Update password and mark as no longer first-time login
    current_user.password_hash = hash_password(new_pw)
    current_user.is_first_login = False
    db.commit()

    ip_address, user_agent = get_client_info(request)
    log_audit(db, current_user.user_id, f"FIRST LOGIN PASSWORD CHANGED",
              ip_address=ip_address, user_agent=user_agent)

    return {
        "message":        "Password changed successfully. You can now access the system.",
        "is_first_login": False
    }


@router.get("/permissions")
async def get_my_permissions(current_user: User = Depends(get_current_user)):
    """
    I-return ang permission matrix ng naka-login na user.
    Ginagamit ng frontend para ipakita/itago ang tabs at buttons.
    """
    from middleware.auth import PERMISSIONS
    role = current_user.role
    perms = PERMISSIONS.get(role, {})
    return {
        "role":        role,
        "permissions": perms,
        "ui_config":   _get_ui_config(role)
    }


def _get_ui_config(role: str) -> dict:
    """
    I-return ang UI configuration para sa bawat role.
    Tinutukoy kung aling tabs ang visible at editable.
    """
    configs = {
        "admin": {
            "tabs": {
                "medical_records":  {"visible": True,  "editable": True},
                "immunizations":    {"visible": True,  "editable": True},
                "health_problems":  {"visible": True,  "editable": True},
                "pregnancy":        {"visible": True,  "editable": True},
            },
            "sidebar": ["dashboard", "patients", "surveillance",
                        "analytics", "bhw_mgmt", "reports", "audit"],
            "can_add_patient":     True,
            "can_archive_patient": True,
            "can_manage_users":    True,
        },
        "bhw": {
            "tabs": {
                "medical_records":  {"visible": True,  "editable": True},
                "immunizations":    {"visible": True,  "editable": True},
                "health_problems":  {"visible": True,  "editable": True},
                "pregnancy":        {"visible": True,  "editable": False},
            },
            "sidebar": ["patients", "surveillance"],
            "can_add_patient":     True,
            "can_archive_patient": False,
            "can_manage_users":    False,
        },
        "midwife": {
            "tabs": {
                "medical_records":  {"visible": True,  "editable": False},
                "immunizations":    {"visible": False, "editable": False},
                "health_problems":  {"visible": True,  "editable": False},
                "pregnancy":        {"visible": True,  "editable": True},
            },
            "sidebar": ["patients"],
            "can_add_patient":     False,
            "can_archive_patient": False,
            "can_manage_users":    False,
        },
        "doctor": {
            "tabs": {
                "medical_records":  {"visible": True,  "editable": True},
                "immunizations":    {"visible": True,  "editable": False},
                "health_problems":  {"visible": True,  "editable": True},
                "pregnancy":        {"visible": True,  "editable": False},
            },
            "sidebar": ["patients", "surveillance"],
            "can_add_patient":     False,
            "can_archive_patient": False,
            "can_manage_users":    False,
        },
        "nurse": {
                "tabs": {
                    "medical_records":  {"visible": True,  "editable": False},
                    "immunizations":    {"visible": True,  "editable": False},
                    "health_problems":  {"visible": True,  "editable": False},
                    "pregnancy":        {"visible": True,  "editable": False},
                },
                "sidebar": [
                    "patients",
                    "surveillance",
                    "analytics",
                    "inventory",
                    "reports"
                ],
                "can_add_patient":     False,
                "can_archive_patient": False,
                "can_manage_users":    False,
            },
    }
    return configs.get(role, configs["bhw"])
# ============================================================
# routers/auth.py — Authentication Routes
# Login, Logout, Password Change
# NO OTP for BHW registration — removed per requirements
# Audit log: LOGIN and LOGOUT only
# ============================================================

import os
from fastapi import APIRouter, Depends, HTTPException, status, Request
from sqlalchemy.orm import Session
from datetime import datetime, timedelta

from database import get_db
from models.models import User, AuditLog
from middleware.auth import (
    get_current_user, log_audit, handle_failed_login,
    reset_failed_attempts, get_client_info
)
from utils.security import (
    verify_password, hash_password,
    create_access_token, validate_password_strength
)

router = APIRouter(prefix="/api/auth", tags=["Authentication"])


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
            remaining = ""
            if user.locked_until:
                mins = int((user.locked_until - datetime.utcnow()).total_seconds() // 60)
                remaining = f" Try again in {mins} minute(s)."
            raise HTTPException(
                status_code=423,
                detail=f"Account is locked.{remaining}"
            )

    if user.status == "inactive":
        raise HTTPException(status_code=403, detail="Account is deactivated. Contact administrator.")

    # Verify password
    if not verify_password(password, user.password_hash):
        result = handle_failed_login(db, user)
        raise HTTPException(status_code=401, detail=result["message"])

    # Successful login
    reset_failed_attempts(db, user)
    user.last_login = datetime.utcnow()
    db.commit()

    # Create JWT token
    token_data = {
        "user_id": user.user_id,
        "email":   user.email,
        "role":    user.role
    }
    access_token = create_access_token(token_data)

    # Log LOGIN event
    log_audit(
        db, user.user_id,
        f"LOGIN - {user.role.upper()}",
        ip_address=ip_address,
        user_agent=user_agent
    )

    expire_minutes = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "60"))

    return {
        "access_token":  access_token,
        "token_type":    "bearer",
        "user_id":       user.user_id,
        "name":          user.name,
        "role":          user.role,
        "email":         user.email,
        "position":      user.position,
        "is_first_login": getattr(user, 'is_first_login', False),
        "expires_in":    expire_minutes * 60
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
    from middleware.auth import PERMISSIONS, get_barangay_name
    from database import get_barangay_name
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
                "health_problems":  {"visible": True,  "editable": False},
                "pregnancy":        {"visible": True,  "editable": False},
            },
            "sidebar": ["patients", "surveillance"],
            "can_add_patient":     True,
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
# ============================================================
# middleware/auth.py - Authentication Middleware
# Para sa pag-verify ng JWT tokens at user permissions
# Ginagamit bilang dependency sa bawat protected na route
# ============================================================

from fastapi import Depends, HTTPException, status, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
import os

from database import get_db
from models.models import User
from utils.security import decode_access_token

# HTTP Bearer token scheme para sa JWT
security = HTTPBearer()

# Ilang beses pwedeng mag-fail bago ma-lock ang account
MAX_FAILED_ATTEMPTS = int(os.getenv("MAX_FAILED_ATTEMPTS", "5"))
LOCKOUT_DURATION_MIN = int(os.getenv("LOCKOUT_DURATION_MINUTES", "15"))


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db)
) -> User:
    """
    Kunin ang kasalukuyang naka-login na user mula sa JWT token.
    Ito ang ginagamit na dependency sa lahat ng protected na routes.
    I-raise ang 401 error kung hindi valid ang token.
    """
    # I-decode ang JWT token
    token = credentials.credentials
    payload = decode_access_token(token)

    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token. Please login again.",
            headers={"WWW-Authenticate": "Bearer"}
        )

    # Kunin ang user_id mula sa token payload
    user_id = payload.get("user_id")
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token payload.",
            headers={"WWW-Authenticate": "Bearer"}
        )

    # Hanapin ang user sa database
    user = db.query(User).filter(User.user_id == user_id).first()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found.",
            headers={"WWW-Authenticate": "Bearer"}
        )

    # Suriin kung active pa ang account
    if user.status != "active":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your account is inactive or locked. Please contact the administrator."
        )

    return user


def require_admin(current_user: User = Depends(get_current_user)) -> User:
    """
    I-require na Admin ang kasalukuyang user.
    Ginagamit sa mga route na para sa Admin lamang.
    I-raise ang 403 error kung hindi Admin.
    """
    if current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied. Admin privileges required."
        )
    return current_user


def require_bhw_or_admin(current_user: User = Depends(get_current_user)) -> User:
    """
    I-require na BHW o Admin ang kasalukuyang user.
    Puwedeng gamitin sa mga route na accessible ng dalawa.
    """
    if current_user.role not in ["admin", "bhw"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied."
        )
    return current_user



    # BHW ay puwede lamang sa kanilang barangay
    if current_user.barangay_id == barangay_id:
        return True

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="You can only access records from your assigned barangay."
    )


def log_audit(
    db: Session,
    user_id,
    action: str,
    ip_address: str = None,
    user_agent: str = None,
    # Keep old params for backward compat but ignore them
    table_name: str = None,
    record_id: int = None
):
    """
    I-record ang login/logout events sa audit log.
    V2: Simplified — LOGIN at LOGOUT events lang.
    """
    try:
        from models.models import AuditLog
        log_entry = AuditLog(
            user_id    = user_id,
            action     = action,
            ip_address = ip_address,
            user_agent = user_agent[:500] if user_agent else None
        )
        db.add(log_entry)
        db.commit()
    except Exception as e:
        print(f"⚠️ Audit log failed: {e}")
        db.rollback()


def handle_failed_login(db: Session, user: User) -> dict:
    """
    Pangasiwaan ang failed login attempt.
    Dagdagan ang failed_attempts counter.
    I-lock ang account kung naabot na ang maximum na attempts.
    Returns ang status message.
    """
    user.failed_attempts += 1

    # Kung naabot na ang maximum na attempts, i-lock ang account
    if user.failed_attempts >= MAX_FAILED_ATTEMPTS:
        user.status       = "locked"
        user.locked_until = datetime.utcnow() + timedelta(minutes=LOCKOUT_DURATION_MIN)
        db.commit()
        return {
            "locked": True,
            "message": f"Account locked for {LOCKOUT_DURATION_MIN} minutes due to too many failed attempts."
        }

    db.commit()
    remaining = MAX_FAILED_ATTEMPTS - user.failed_attempts
    return {
        "locked": False,
        "message": f"Invalid password. {remaining} attempt(s) remaining before lockout."
    }


def reset_failed_attempts(db: Session, user: User):
    """
    I-reset ang failed login attempts pagkatapos ng matagumpay na login.
    Ginagamit rin ito pagkatapos ma-unlock ng Admin ang account.
    """
    user.failed_attempts = 0
    user.locked_until    = None
    if user.status == "locked":
        user.status = "active"
    db.commit()


def get_client_info(request: Request) -> tuple[str, str]:
    """
    Kunin ang IP address at User-Agent ng client.
    Para sa audit logging at security monitoring.
    """
    # Kunin ang real IP (kahit nasa proxy)
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        ip_address = forwarded_for.split(",")[0].strip()
    else:
        ip_address = request.client.host if request.client else "Unknown"

    user_agent = request.headers.get("User-Agent", "Unknown")
    return ip_address, user_agent


# ============================================================
# RBAC — Role-Based Access Control Helpers
# ============================================================

# Permission matrix per role
# Format: role -> {resource: "edit" | "view" | "none"}
PERMISSIONS = {
    "admin": {
        "medical_records": "edit",
        "immunization":    "edit",
        "pregnancy":       "edit",
        "health_problems": "edit",
        "users":           "edit",
    },
    "bhw": {
        "medical_records": "edit",
        "immunization":    "edit",
        "pregnancy":       "view",
        "health_problems": "edit",
        "users":           "none",
    },
    "midwife": {
        "medical_records": "view",
        "immunization":    "none",
        "pregnancy":       "edit",
        "health_problems": "view",
        "users":           "none",
    },
    "doctor": {
        "medical_records": "edit",
        "immunization":    "view",
        "pregnancy":       "view",
        "health_problems": "view",
        "users":           "none",
        },  
    "nurse": {
        "medical_records": "view",
        "immunization":    "view",
        "pregnancy":       "view",
        "health_problems": "view",
        "users":           "none",
},
}


def get_permission(role: str, resource: str) -> str:
    """Return permission level: 'edit', 'view', or 'none'."""
    return PERMISSIONS.get(role, {}).get(resource, "none")


def require_permission(resource: str, level: str = "view"):
    """
    FastAPI dependency — checks if current user has required permission.
    level = "view" (can view) or "edit" (can modify)
    """
    from fastapi import Depends, HTTPException
    async def _check(current_user=Depends(get_current_user)):
        perm = get_permission(current_user.role, resource)
        if level == "edit" and perm != "edit":
            raise HTTPException(
                status_code=403,
                detail=f"Your role ({current_user.role}) does not have edit access to {resource}."
            )
        if level == "view" and perm == "none":
            raise HTTPException(
                status_code=403,
                detail=f"Your role ({current_user.role}) does not have access to {resource}."
            )
        return current_user
    return _check
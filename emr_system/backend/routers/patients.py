# ============================================================
# routers/patients.py — Patient CRUD
# No barangay FK — single barangay per DB instance
# ============================================================

import io
import os
import re
import secrets
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request, UploadFile, File
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool
from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy.orm import Session
from sqlalchemy import or_
from typing import Optional
from datetime import date

from database import get_db, engine
from models.models import Patient, User
from middleware.auth import get_current_user, require_admin
from websocket_manager import manager
router = APIRouter(prefix="/api/patients", tags=["Patients"])

# ── Patient photo settings ──────────────────────────────────
# Stored OUTSIDE the frontend folder, so it is never served as a static file.
# Photos can only be read through the authenticated GET /{id}/photo endpoint.
PHOTO_DIR        = Path(os.getenv("PATIENT_PHOTO_DIR") or
                        Path(__file__).resolve().parent.parent / "private_uploads" / "patient_photos")
MAX_PHOTO_BYTES  = 2 * 1024 * 1024        # hard server limit: 2 MB
MAX_PHOTO_SIDE   = 800                    # stored size: at most 800 x 800 px
MIN_PHOTO_SIDE   = 64
_PHOTO_NAME_RE   = re.compile(r"^[0-9a-f]{32}\.jpg$")
Image.MAX_IMAGE_PIXELS = 25_000_000       # refuse decompression bombs


def _calc_age(birthdate: date) -> int:
    today = date.today()
    age   = today.year - birthdate.year
    if (today.month, today.day) < (birthdate.month, birthdate.day):
        age -= 1
    return age


def _fmt(p: Patient) -> dict:
    """Format patient record as dict."""
    return {
        "patient_id":       p.patient_id,
        "last_name":        p.last_name,
        "first_name":       p.first_name,
        "middle_name":      p.middle_name,
        "birthdate":        str(p.birthdate),
        "sex":              p.sex,
        "civil_status":     p.civil_status,
        "address":          p.address,
        "contact_number":   p.contact_number,
        "philhealth_no":    p.philhealth_no,
        "occupation":       p.occupation,
        "mother_name":      p.mother_name,
        "father_name":      p.father_name,
        "guardian_contact": p.guardian_contact,
        "age":              _calc_age(p.birthdate),
        "is_archived":      p.is_archived,
        "created_at":       str(p.created_at),
        "has_photo":        bool(getattr(p, "photo_filename", None)),
        "photo_v":          (getattr(p, "photo_filename", None) or "")[:8] or None   # cache-buster only
    }


@router.get("/")
async def get_patients(
    db:       Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    search:   Optional[str] = Query(None),
    sex:      Optional[str] = Query(None),
    age_from: Optional[int] = Query(None),
    age_to:   Optional[int] = Query(None),
    skip:     int = Query(0, ge=0),
    limit:    int = Query(50, ge=1, le=200)
):
    """
    Kunin ang listahan ng mga pasyente.
    May search (pangalan, contact) at sex/age filter.
    """
    query = db.query(Patient).filter(Patient.is_archived == False)

    if search:
        term = f"%{search.strip()}%"
        query = query.filter(
            or_(
                Patient.last_name.ilike(term),
                Patient.first_name.ilike(term),
                Patient.contact_number.ilike(term)
            )
        )
    if sex:
        query = query.filter(Patient.sex == sex)

    # Age filter — convert age to birthdate range
    if age_from is not None or age_to is not None:
        today = date.today()
        if age_to is not None:
            min_birth = date(today.year - age_to - 1, today.month, today.day)
            query = query.filter(Patient.birthdate >= min_birth)
        if age_from is not None:
            max_birth = date(today.year - age_from, today.month, today.day)
            query = query.filter(Patient.birthdate <= max_birth)

    patients = query.order_by(Patient.last_name, Patient.first_name).offset(skip).limit(limit).all()
    return [_fmt(p) for p in patients]


@router.post("/", status_code=201)
async def create_patient(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Mag-register ng bagong pasyente."""
    body = await request.json()

    required = ["last_name", "first_name", "birthdate", "sex", "address"]
    for field in required:
        if not body.get(field):
            raise HTTPException(status_code=400, detail=f"{field} is required.")

    # Validate birthdate not in future
    try:
        bd = date.fromisoformat(body["birthdate"])
        if bd > date.today():
            raise HTTPException(status_code=400, detail="Birthdate cannot be in the future.")
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid birthdate format.")

    p = Patient(
        last_name        = body["last_name"].strip().title(),
        first_name       = body["first_name"].strip().title(),
        middle_name      = (body.get("middle_name") or "").strip().title() or None,
        birthdate        = body["birthdate"],
        sex              = body["sex"],
        civil_status     = body.get("civil_status") or None,
        address          = body["address"].strip(),
        contact_number   = body.get("contact_number") or None,
        philhealth_no    = body.get("philhealth_no")  or None,
        occupation       = body.get("occupation")     or None,
        mother_name      = body.get("mother_name")    or None,
        father_name      = body.get("father_name")    or None,
        guardian_contact = body.get("guardian_contact") or None
    )
    db.add(p)
    db.commit()
    db.refresh(p)

    patient_data = _fmt(p)

    print("📢 Broadcasting patient_created event...")
    print(f"🔌 Connected clients: {len(manager.active_connections)}")

    await manager.broadcast_event(
        "patient_created",
        "patient",
        "created",
        patient=patient_data,
        patient_id=p.patient_id,
        user_id=current_user.user_id
    )

    print("✅ patient_created event broadcasted")

    return patient_data


@router.get("/stats")
async def get_patient_stats(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Summary stats para sa dashboard."""
    base  = db.query(Patient).filter(Patient.is_archived == False)
    total = base.count()
    male  = base.filter(Patient.sex == "Male").count()
    fem   = base.filter(Patient.sex == "Female").count()
    return {"total_patients": total, "male": male, "female": fem}


@router.get("/{patient_id}")
async def get_patient(
    patient_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Kunin ang detalye ng isang pasyente."""
    p = db.query(Patient).filter(
        Patient.patient_id == patient_id,
        Patient.is_archived == False
    ).first()
    if not p:
        raise HTTPException(status_code=404, detail="Patient not found.")
    return _fmt(p)


@router.put("/{patient_id}")
async def update_patient(
    patient_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """I-update ang patient information."""
    p = db.query(Patient).filter(
        Patient.patient_id == patient_id,
        Patient.is_archived == False
    ).first()
    if not p:
        raise HTTPException(status_code=404, detail="Patient not found.")

    body = await request.json()
    fields = [
        "last_name","first_name","middle_name","birthdate","sex",
        "civil_status","address","contact_number","philhealth_no",
        "occupation","mother_name","father_name","guardian_contact"
    ]
    for f in fields:
        if f in body:
            setattr(p, f, body[f] or None if f not in ["last_name","first_name","address"] else body[f])

    db.commit()
    db.refresh(p)

    patient_data = _fmt(p)

    # Notify all connected users
    await manager.broadcast_event(
    "patient_updated",
    "patient",
    "updated",
    patient=patient_data,
    patient_id=p.patient_id,
    user_id=current_user.user_id
)

    return patient_data


@router.delete("/{patient_id}")
async def archive_patient(
    patient_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin)
):
    """I-archive ang patient record (Admin only — soft delete)."""
    p = db.query(Patient).filter(Patient.patient_id == patient_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Patient not found.")
    p.is_archived = True
    db.commit()

    # Notify all connected users
    await manager.broadcast_event(
    "patient_deleted",
    "patient",
    "deleted",
    patient_id=patient_id,
    user_id=current_user.user_id
)

    return {
        "message": f"Patient {p.last_name}, {p.first_name} archived."
    }

# ============================================================
# PATIENT PHOTO (JPEG only, max 2 MB, re-encoded by the server)
# ============================================================

def _get_active_patient(db: Session, patient_id: int) -> Patient:
    p = db.query(Patient).filter(
        Patient.patient_id == patient_id,
        Patient.is_archived == False
    ).first()
    if not p:
        raise HTTPException(status_code=404, detail="Patient not found.")
    return p


def _photo_file(filename: Optional[str]) -> Optional[Path]:
    """Return a safe path inside PHOTO_DIR, or None. Never trusts the stored value blindly."""
    if not filename or not _PHOTO_NAME_RE.fullmatch(filename):
        return None
    path = (PHOTO_DIR / filename).resolve()
    return path if path.parent == PHOTO_DIR.resolve() else None


def _process_jpeg(data: bytes) -> bytes:
    """
    Validate and RE-ENCODE the upload. The stored file is always a fresh JPEG made by us:
    hidden payloads, scripts and EXIF data (including GPS location) are not carried over.
    Raises ValueError with a user-safe message if the file is not acceptable.
    """
    if data[:3] != b"\xff\xd8\xff":
        raise ValueError("The file is not a valid JPEG image.")
    try:
        with Image.open(io.BytesIO(data)) as im:
            if im.format != "JPEG":
                raise ValueError("The file is not a valid JPEG image.")
            im.load()                                   # fully decode: rejects truncated/corrupt files
            im = ImageOps.exif_transpose(im)            # apply phone rotation before metadata is dropped
            if min(im.size) < MIN_PHOTO_SIDE:
                raise ValueError(f"The image is too small (minimum {MIN_PHOTO_SIDE} px).")
            im = im.convert("RGB")
            im.thumbnail((MAX_PHOTO_SIDE, MAX_PHOTO_SIDE))
            out = io.BytesIO()
            im.save(out, format="JPEG", quality=85, optimize=True)   # no exif= argument => metadata dropped
            return out.getvalue()
    except ValueError:
        raise
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, SyntaxError):
        raise ValueError("The file is not a valid JPEG image.")


def _require_photo_column():
    """
    The DB column alone is not enough: the SQLAlchemy model must map it too.
    Without this check, an unmapped attribute is silently ignored on commit,
    the upload looks successful, and the photo never appears.
    """
    if "photo_filename" not in Patient.__table__.columns:
        raise HTTPException(
            status_code=500,
            detail=("Server setup incomplete: add  photo_filename = Column(String(64), nullable=True)  "
                    "to the Patient model in models/models.py, then restart the server.")
        )


def _remove_file(path: Optional[Path]):
    try:
        if path and path.exists():
            path.unlink()
    except OSError:
        pass


@router.post("/{patient_id}/photo")
async def upload_patient_photo(
    patient_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Upload or replace a patient's photo. JPEG only, max 2 MB."""
    _require_photo_column()
    patient = _get_active_patient(db, patient_id)

    name = (file.filename or "").lower()
    if not name.endswith((".jpg", ".jpeg")) or (file.content_type or "").lower() != "image/jpeg":
        raise HTTPException(status_code=400, detail="Only JPG images are allowed.")

    data = await file.read(MAX_PHOTO_BYTES + 1)         # never read more than the limit
    if len(data) > MAX_PHOTO_BYTES:
        raise HTTPException(status_code=413, detail="Photo is too large (maximum 2 MB).")
    if not data:
        raise HTTPException(status_code=400, detail="The file is empty.")

    try:
        clean = await run_in_threadpool(_process_jpeg, data)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    PHOTO_DIR.mkdir(parents=True, exist_ok=True)
    new_name = secrets.token_hex(16) + ".jpg"           # random name; the uploaded filename is never used
    dest = PHOTO_DIR / new_name
    tmp  = PHOTO_DIR / (new_name + ".tmp")
    try:
        tmp.write_bytes(clean)
        os.replace(tmp, dest)
        try: os.chmod(dest, 0o600)
        except OSError: pass
    except OSError as e:
        _remove_file(tmp)
        print(f"❌ Patient photo: cannot write file to {PHOTO_DIR}: {e!r}")
        raise HTTPException(status_code=500, detail="The server could not write the photo file (see the server console).")

    old = _photo_file(getattr(patient, "photo_filename", None))
    patient.photo_filename = new_name
    try:
        db.commit()
    except Exception as e:
        db.rollback()
        _remove_file(dest)
        print(f"❌ Patient photo: database commit failed for patient {patient_id}: {e!r}")
        raise HTTPException(status_code=500, detail="The photo could not be recorded in the database (see the server console).")
    _remove_file(old)

    db.refresh(patient)
    await manager.broadcast_event("patient_updated", "patient", "updated",
                                  patient=_fmt(patient), patient_id=patient.patient_id,
                                  user_id=current_user.user_id)
    print(f"📷 Patient photo saved: patient {patient_id}, {len(data)} bytes in -> {len(clean)} bytes stored as {new_name}")
    return {"message": "Photo saved.", "has_photo": bool(patient.photo_filename), "photo_v": new_name[:8]}


@router.get("/{patient_id}/photo")
async def get_patient_photo(
    patient_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Serve the photo to logged-in staff only."""
    patient = _get_active_patient(db, patient_id)
    stored = getattr(patient, "photo_filename", None)
    if not stored:
        raise HTTPException(status_code=404, detail="No photo is recorded for this patient.")
    path = _photo_file(stored)
    if not path:
        print(f"⚠️ Patient photo: invalid stored name for patient {patient_id}: {stored!r}")
        raise HTTPException(status_code=404, detail="The stored photo name is invalid.")
    if not path.exists():
        print(f"⚠️ Patient photo: file missing for patient {patient_id}. Expected at: {path}")
        raise HTTPException(status_code=404, detail="The photo file is missing on the server (see the server console).")
    return FileResponse(path, media_type="image/jpeg",
                        headers={"Cache-Control": "private, no-cache",
                                 "Content-Disposition": "inline"})


@router.delete("/{patient_id}/photo")
async def delete_patient_photo(
    patient_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Remove a patient's photo."""
    _require_photo_column()
    patient = _get_active_patient(db, patient_id)
    old = _photo_file(getattr(patient, "photo_filename", None))
    patient.photo_filename = None
    db.commit()
    _remove_file(old)
    db.refresh(patient)
    await manager.broadcast_event("patient_updated", "patient", "updated",
                                  patient=_fmt(patient), patient_id=patient.patient_id,
                                  user_id=current_user.user_id)
    return {"message": "Photo removed.", "has_photo": False}


# ── Startup self-check: tells you in the server console if photo upload cannot work ──
def _photo_setup_problems() -> list:
    problems = []
    if "photo_filename" not in Patient.__table__.columns:
        problems.append("models/models.py: class Patient has no photo_filename. "
                        "Add:  photo_filename = Column(String(64), nullable=True)")
    try:
        from sqlalchemy import inspect as _inspect
        cols = {c["name"] for c in _inspect(engine).get_columns(Patient.__tablename__)}
        if "photo_filename" not in cols:
            problems.append(f"database: table '{Patient.__tablename__}' in the database this server is connected to "
                            "has no photo_filename column. Run migrations/add_patient_photo.sql on THIS database.")
    except Exception as e:
        problems.append(f"could not inspect the database ({e.__class__.__name__}: {e})")
    try:
        PHOTO_DIR.mkdir(parents=True, exist_ok=True)
        probe = PHOTO_DIR / ".write_test"
        probe.write_bytes(b"x"); probe.unlink()
    except OSError as e:
        problems.append(f"cannot write to {PHOTO_DIR}: {e}")
    return problems


def _report_photo_setup():
    try:
        problems = _photo_setup_problems()
    except Exception as e:
        problems = [f"self-check failed: {e!r}"]
    if problems:
        print("❌ Patient photo upload is NOT ready:")
        for p in problems:
            print("   -", p)
    else:
        print(f"✅ Patient photo upload ready (folder: {PHOTO_DIR})")


_report_photo_setup()
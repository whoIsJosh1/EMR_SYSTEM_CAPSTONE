# ============================================================
# routers/patients.py — Patient CRUD
# No barangay FK — single barangay per DB instance
# ============================================================

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session
from sqlalchemy import or_
from typing import Optional
from datetime import date

from database import get_db
from models.models import Patient, User
from middleware.auth import get_current_user, require_admin
from websocket_manager import manager
router = APIRouter(prefix="/api/patients", tags=["Patients"])


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
        "created_at":       str(p.created_at)
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
# ============================================================
# main.py — FastAPI Application v2
# - No OTP for BHW registration
# - No barangay dropdown (single-barangay per instance)
# - Clean routes: auth, users, patients, analytics, reports
# ============================================================

import os, re, time
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse, FileResponse
from sqlalchemy.orm import Session
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from dotenv import load_dotenv
from websocket_manager import manager
load_dotenv()

from database import engine, get_db, test_connection, get_barangay_name, Base
from models.models import (
    User, Patient, HealthProblem, Pregnancy,
    MedicalRecord, Immunization, Disease, DiseaseCase, AuditLog,
    InventoryItem, InventoryTransaction
)
from middleware.auth import (
    get_current_user, require_admin, log_audit, get_client_info,
    require_permission, get_permission
)
from routers.auth      import router as auth_router
from routers.users     import router as users_router
from routers.patients  import router as patients_router
from routers.analytics import router as analytics_router, ensure_case_schema
from routers.reports   import router as reports_router
from routers.inventory import router as inventory_router
from routers.prescriptions import router as prescriptions_router
from routers.websocket import router as websocket_router
from routers import ai_insights
from routers import ai_resources   # Resource Allocation Analysis (admin only)
from routers.diseases import router as diseases_router, ensure_schema as ensure_disease_schema
from routers.analytics_geo import router as analytics_geo_router   # Geographical Hotspots tab

# ── Rate limiter ──────────────────────────────────────────────────────────
limiter = Limiter(key_func=get_remote_address)

# ── Inline routers ────────────────────────────────────────────────────────
from fastapi import APIRouter
from typing import Optional

# Medical Records
mr_router = APIRouter(prefix="/api/medical-records", tags=["Medical Records"])

@mr_router.post("/")
async def create_medical_record(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission('medical_records', 'edit'))
):
    """Mag-encode ng bagong medical record. Nag-ti-trigger ng auto disease case counting."""
    body = await request.json()
    patient_id = body.get("patient_id")

    patient = db.query(Patient).filter(
        Patient.patient_id == patient_id,
        Patient.is_archived == False
    ).first()
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found.")

    rec = MedicalRecord(
        patient_id       = patient_id,
        visit_date       = body.get("visit_date"),
        chief_complaint  = body.get("chief_complaint"),
        symptoms         = body.get("symptoms"),
        diagnosis        = body.get("diagnosis"),
        treatment        = body.get("treatment"),
        blood_pressure   = body.get("blood_pressure"),
        temperature      = body.get("temperature"),
        weight_kg        = body.get("weight_kg"),
        height_cm        = body.get("height_cm"),
        heart_rate       = body.get("heart_rate"),
        respiratory_rate = body.get("respiratory_rate"),
        lmp              = body.get("lmp") if patient.sex == "Female" else None,
        notes            = body.get("notes"),
        follow_up_required = body.get("follow_up_required", False),
        follow_up_date     = body.get("follow_up_date") or None,
        follow_up_time     = body.get("follow_up_time") or None,
        user_id          = current_user.user_id
    )
    db.add(rec)
    db.commit()
    db.refresh(rec)

    await manager.broadcast_event(
        "medical_record_created",
        "medical_record",
        "created",
        record_id=rec.record_id,
        patient_id=rec.patient_id,
        user_id=current_user.user_id
    )
    # Auto-count disease cases from diagnosis field
    diagnosis = body.get("diagnosis", "") or ""
    if diagnosis:
        _auto_count_cases(db, diagnosis, patient_id, body.get("visit_date"), current_user.user_id, role=current_user.role)

    return {"message": "Medical record saved.", "record_id": rec.record_id}


@mr_router.get("/patient/{patient_id}")
async def get_patient_records(
    patient_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission('medical_records', 'view'))
):
    """Kunin ang lahat ng medical records ng isang pasyente."""
    records = db.query(MedicalRecord).filter(
        MedicalRecord.patient_id == patient_id
    ).order_by(MedicalRecord.visit_date.desc()).all()

    return [
        {
            "record_id":        r.record_id,
            "visit_date":       str(r.visit_date),
            "chief_complaint":  r.chief_complaint,
            "symptoms":         r.symptoms,
            "diagnosis":        r.diagnosis,
            "treatment":        r.treatment,
            "blood_pressure":   r.blood_pressure,
            "temperature":      float(r.temperature) if r.temperature else None,
            "weight_kg":        float(r.weight_kg) if r.weight_kg else None,
            "height_cm":        float(r.height_cm) if r.height_cm else None,
            "heart_rate":       r.heart_rate,
            "respiratory_rate": r.respiratory_rate,
            "lmp":              str(r.lmp) if r.lmp else None,
            "notes":            r.notes,
            "follow_up_required": bool(r.follow_up_required),
            "follow_up_date":     str(r.follow_up_date) if r.follow_up_date else None,
            "follow_up_time":     str(r.follow_up_time) if r.follow_up_time else None,
            "encoder":            r.encoder.name if r.encoder else "—",
            "created_at":       str(r.created_at)
        }
        for r in records
    ]


# ============================================================
# Update Medical Record / Doctor Diagnosis
# ============================================================

@mr_router.put("/{record_id}")
async def update_medical_record(
    record_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(
        require_permission('medical_records', 'edit')
    )
):
    """Update an existing medical record, especially doctor diagnosis."""

    record = db.query(MedicalRecord).filter(
        MedicalRecord.record_id == record_id
    ).first()

    if not record:
        raise HTTPException(
            status_code=404,
            detail="Medical record not found."
        )

    body = await request.json()

    fields = [
        "visit_date",
        "chief_complaint",
        "symptoms",
        "diagnosis",
        "treatment",
        "blood_pressure",
        "temperature",
        "weight_kg",
        "height_cm",
        "heart_rate",
        "respiratory_rate",
        "notes",
        "follow_up_required",
        "follow_up_date",
        "follow_up_time"
    ]

    for field in fields:
        if field in body:
            setattr(record, field, body[field])

    # LMP is only applicable to female patients
    if "lmp" in body:
        patient = db.query(Patient).filter(
            Patient.patient_id == record.patient_id
        ).first()

        if patient and patient.sex == "Female":
            record.lmp = body["lmp"] or None

    db.commit()
    db.refresh(record)

    
    await manager.broadcast_event(
        "medical_record_updated",
        "medical_record",
        "updated",
        record_id=record.record_id,
        patient_id=record.patient_id,
        user_id=current_user.user_id,
        diagnosis=record.diagnosis
    )

    # If diagnosis was added/changed, update disease case counting
    diagnosis = body.get("diagnosis", "") or ""

    if diagnosis:
        _auto_count_cases(
            db,
            diagnosis,
            record.patient_id,
            record.visit_date,
            current_user.user_id,
            role=current_user.role
        )

    return {
        "message": "Medical record updated successfully.",
        "record_id": record.record_id
    }


# Immunization Router
immun_router = APIRouter(
    prefix="/api/immunizations",
    tags=["Immunizations"]
)
# Immunization Router
immun_router = APIRouter(prefix="/api/immunizations", tags=["Immunizations"])

@immun_router.post("/")
async def create_immunization(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission('immunization', 'edit'))
):
    """Mag-record ng bagong immunization para sa pasyente."""
    body = await request.json()
    patient_id = body.get("patient_id")

    patient = db.query(Patient).filter(Patient.patient_id == patient_id).first()
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found.")

    immun = Immunization(
        patient_id      = patient_id,
        vaccine_name    = body.get("vaccine_name"),
        date_given      = body.get("date_given"),
        dose_number     = body.get("dose_number", 1),
        administered_by = body.get("administered_by"),
        batch_number    = body.get("batch_number"),
        next_schedule   = body.get("next_schedule"),
        remarks         = body.get("remarks"),
        user_id         = current_user.user_id
    )
    db.add(immun)
    db.commit()
    db.refresh(immun)

    await manager.broadcast_event(
    "immunization_created",
    "immunization",
    "created",
    immunization_id=immun.immunization_id,
    patient_id=immun.patient_id,
    user_id=current_user.user_id
)
    return {"message": "Immunization saved.", "immunization_id": immun.immunization_id}


@immun_router.get("/patient/{patient_id}")
async def get_patient_immunizations(
    patient_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission('immunization', 'view'))
):
    """Kunin ang lahat ng immunization records ng pasyente."""
    records = db.query(Immunization).filter(
        Immunization.patient_id == patient_id
    ).order_by(Immunization.date_given.desc()).all()

    return [
        {
            "immunization_id": r.immunization_id,
            "vaccine_name":    r.vaccine_name,
            "date_given":      str(r.date_given),
            "dose_number":     r.dose_number,
            "administered_by": r.administered_by,
            "batch_number":    r.batch_number,
            "next_schedule":   str(r.next_schedule) if r.next_schedule else None,
            "remarks":         r.remarks,
            "encoder":         r.encoder.name       if r.encoder       else "—"
        }
        for r in records
    ]


# Health Problems Router
hp_router = APIRouter(prefix="/api/health-problems", tags=["Health Problems"])

@hp_router.get("/{patient_id}")
async def get_health_problems(
    patient_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission('health_problems', 'view'))
):
    """Kunin ang health problems ng pasyente."""
    hp = db.query(HealthProblem).filter(HealthProblem.patient_id == patient_id).first()
    if not hp:
        return {"patient_id": patient_id, "allergies": None,
                "has_asthma": False, "chronic_diseases": None, "other_concerns": None}
    return {
        "problem_id":       hp.problem_id,
        "patient_id":       hp.patient_id,
        "allergies":        hp.allergies,
        "has_asthma":       bool(hp.has_asthma),
        "chronic_diseases": hp.chronic_diseases,
        "other_concerns":   hp.other_concerns,
        "updated_at":       str(hp.updated_at) if hp.updated_at else None
    }


@hp_router.post("/{patient_id}")
async def upsert_health_problems(
    patient_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission('health_problems', 'edit'))
):
    """I-save ang health problems (upsert — create or update)."""
    body = await request.json()
    hp = db.query(HealthProblem).filter(HealthProblem.patient_id == patient_id).first()
    if hp:
        hp.allergies        = body.get("allergies")
        hp.has_asthma       = body.get("has_asthma", False)
        hp.chronic_diseases = body.get("chronic_diseases")
        hp.other_concerns   = body.get("other_concerns")
    else:
        hp = HealthProblem(
            patient_id       = patient_id,
            allergies        = body.get("allergies"),
            has_asthma       = body.get("has_asthma", False),
            chronic_diseases = body.get("chronic_diseases"),
            other_concerns   = body.get("other_concerns")
        )
    db.add(hp)

    db.commit()

    await manager.broadcast_event(
        "health_problem_updated",
        "health_problem",
        "updated",
        patient_id=patient_id,
        user_id=current_user.user_id
    )

    return {"message": "Health problems saved."}


# Pregnancy Router
preg_router = APIRouter(prefix="/api/pregnancy", tags=["Pregnancy"])

@preg_router.get("/{patient_id}")
async def get_pregnancy(
    patient_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission('pregnancy', 'view'))
):
    """Kunin ang pregnancy records ng pasyente (female only)."""
    patient = db.query(Patient).filter(Patient.patient_id == patient_id).first()
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found.")
    if patient.sex != "Female":
        raise HTTPException(status_code=400, detail="Pregnancy records are for female patients only.")

    records = db.query(Pregnancy).filter(
        Pregnancy.patient_id == patient_id
    ).order_by(Pregnancy.created_at.desc()).all()

    return [
        {
            "pregnancy_id":       r.pregnancy_id,
            "status":             r.status,
            "gravida":            r.gravida,
            "para":               r.para,
            "lmp":                str(r.lmp)                if r.lmp                else None,
            "expected_due_date":  str(r.expected_due_date)  if r.expected_due_date  else None,
            "delivery_date":      str(r.delivery_date)      if r.delivery_date      else None,
            "delivery_type":      r.delivery_type,
            "birth_outcome":      r.birth_outcome,
            "prenatal_visits":    r.prenatal_visits,
            "last_prenatal_date": str(r.last_prenatal_date) if r.last_prenatal_date else None,
            "attending_physician":r.attending_physician,
            "remarks":            r.remarks,
            "encoder":            r.encoder.name            if r.encoder            else "—",
            "created_at":         str(r.created_at)
        }
        for r in records
    ]


@preg_router.post("/{patient_id}")
async def save_pregnancy(
    patient_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission('pregnancy', 'edit'))
):
    """Mag-save ng pregnancy record (create new entry)."""
    patient = db.query(Patient).filter(Patient.patient_id == patient_id).first()
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found.")
    if patient.sex != "Female":
        raise HTTPException(status_code=400, detail="Female patients only.")

    body = await request.json()
    preg = Pregnancy(
        patient_id          = patient_id,
        status              = body.get("status", "Pregnant"),
        gravida             = body.get("gravida"),
        para                = body.get("para"),
        lmp                 = body.get("lmp")                or None,
        expected_due_date   = body.get("expected_due_date")  or None,
        delivery_date       = body.get("delivery_date")      or None,
        delivery_type       = body.get("delivery_type")      or None,
        birth_outcome       = body.get("birth_outcome"),
        prenatal_visits     = body.get("prenatal_visits", 0),
        last_prenatal_date  = body.get("last_prenatal_date") or None,
        attending_physician = body.get("attending_physician"),
        remarks             = body.get("remarks"),
        user_id             = current_user.user_id
    )
    db.add(preg)
    db.commit()
    db.refresh(preg)
    return {"message": "Pregnancy record saved.", "pregnancy_id": preg.pregnancy_id}


@preg_router.put("/{pregnancy_id}")
async def update_pregnancy(
    pregnancy_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission('pregnancy', 'edit'))
):
    """I-update ang isang pregnancy record."""
    preg = db.query(Pregnancy).filter(Pregnancy.pregnancy_id == pregnancy_id).first()
    if not preg:
        raise HTTPException(status_code=404, detail="Pregnancy record not found.")
    body = await request.json()
    for field in ["status","gravida","para","birth_outcome","prenatal_visits","attending_physician","remarks"]:
        if field in body:
            setattr(preg, field, body[field])
    for date_field in ["lmp","expected_due_date","delivery_date","last_prenatal_date"]:
        if date_field in body:
            setattr(preg, date_field, body[date_field] or None)
    if "delivery_type" in body:
        preg.delivery_type = body["delivery_type"] or None
    db.commit()
    return {"message": "Pregnancy record updated."}


# Disease router → routers/diseases.py (dynamic, DB-driven)


# Audit Log Router
audit_router = APIRouter(prefix="/api/audit-logs", tags=["Audit Logs"])

@audit_router.get("/")
async def get_audit_logs(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
    skip: int = 0, limit: int = 100
):
    """Kunin ang audit log — LOGIN at LOGOUT events lamang (Admin only)."""
    logs = db.query(AuditLog).filter(
        AuditLog.action.in_(["LOGIN - ADMIN", "LOGIN - BHW", "LOGOUT"])
    ).order_by(AuditLog.date_time.desc()).offset(skip).limit(limit).all()

    return [
        {
            "log_id":     l.log_id,
            "user_name":  l.user.name if l.user else "Unknown",
            "user_role":  l.user.role if l.user else "—",
            "action":     l.action,
            "ip_address": l.ip_address,
            "date_time":  str(l.date_time)
        }
        for l in logs
    ]


# ── Auto disease case counting (DB-driven — walang hardcoded na sakit) ────
CASE_SOURCE_ROLES = ("doctor",)   # tanging diagnosis ng doctor ang nagiging surveillance case

def _auto_count_cases(db: Session, diagnosis: str, patient_id: int, date_recorded, user_id: int, role=None):
    """
    I-match ang diagnosis text sa LAHAT ng active na sakit sa `disease` table
    gamit ang pangalan, ICD code, at aliases nito. Kapag nagdagdag ang admin ng
    bagong sakit sa Manage Diseases, awtomatiko na itong mabibilang dito.
    """
    import re
    from sqlalchemy import text as sql
    text_ = (diagnosis or "").lower()
    if not text_.strip() or role not in CASE_SOURCE_ROLES:
        return

    rows = db.execute(sql(
        "SELECT disease_id, disease_name, icd_code, aliases, is_notifiable FROM disease WHERE is_active = 1"
    )).mappings().all()

    notif = {r["disease_id"]: bool(r["is_notifiable"]) for r in rows}
    detected = set()
    for r in rows:
        terms = [r["disease_name"], r["icd_code"]] + (r["aliases"] or "").split(",")
        for t in terms:
            t = (t or "").strip().lower()
            if len(t) < 2:
                continue
            if re.search(r"(?<!\w)" + re.escape(t) + r"(?!\w)", text_):
                detected.add(r["disease_id"])
                break

    added = False
    for did in detected:
        # iwas double-count kapag ina-update ang parehong record
        exists = db.query(DiseaseCase).filter(
            DiseaseCase.disease_id == did,
            DiseaseCase.patient_id == patient_id,
            DiseaseCase.date_recorded == date_recorded
        ).first()
        if exists:
            continue
        db.add(DiseaseCase(
            disease_id=did, patient_id=patient_id, date_recorded=date_recorded,
            number_of_cases=1, remarks=f"Auto-recorded from diagnosis: {diagnosis[:100]}",
            user_id=user_id,
            case_status="Suspected" if notif.get(did) else "Confirmed"   # notifiable lang ang dumadaan sa validation
        ))
        added = True
    if added:
        db.commit()


# ── Application lifespan ──────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    print(f"🚀 EMR System starting — {get_barangay_name()}")

    if test_connection():
        InventoryItem.__table__.create(bind=engine, checkfirst=True)
        InventoryTransaction.__table__.create(bind=engine, checkfirst=True)
        print("✅ Inventory tables are ready.")
        ensure_disease_schema(engine)
        ensure_case_schema(engine)
        print("✅ Disease tables are ready.")

    print("✅ Ready.")
    yield
    print("🛑 Shutting down.")


# ── App init ──────────────────────────────────────────────────────────────
app = FastAPI(
    title       = f"EMR System — {get_barangay_name()}",
    description = "Barangay-level Electronic Medical Records",
    version     = "2.0.0",
    lifespan    = lifespan,
    docs_url    = "/api/docs" if os.getenv("DEBUG", "False").lower() == "true" else None,
    redoc_url=  "/redoc"
)

# Rate limiter
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

# CORS
allowed_origins = os.getenv("ALLOWED_ORIGINS", "http://localhost:8000").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins     = allowed_origins,
    allow_credentials = True,
    allow_methods     = ["GET","POST","PUT","DELETE"],
    allow_headers     = ["Authorization","Content-Type"],
    max_age           = 600
)

# Reject oversized patient-photo uploads BEFORE the request body is read.
# (Registered before the security-headers middleware so that middleware still wraps this response.)
_PHOTO_PATH        = re.compile(r"^/api/patients/\d+/photo$")
_PHOTO_MAX_REQUEST = 3 * 1024 * 1024      # 2 MB file limit + multipart overhead

@app.middleware("http")
async def limit_photo_upload_size(request: Request, call_next):
    if request.method == "POST" and _PHOTO_PATH.match(request.url.path):
        cl = request.headers.get("content-length")
        if cl is None or not cl.isdigit():
            return JSONResponse({"detail": "Content-Length is required."}, status_code=411)
        if int(cl) > _PHOTO_MAX_REQUEST:
            return JSONResponse({"detail": "Photo is too large (maximum 2 MB)."}, status_code=413)
    return await call_next(request)

# Security headers middleware
@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Frame-Options"]        = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-XSS-Protection"]       = "1; mode=block"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' 'unsafe-eval' "
            "https://cdn.jsdelivr.net https://cdnjs.cloudflare.com; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; "
        "img-src 'self' data: blob:; "
        "connect-src 'self'; worker-src 'self' blob:;"
    )
    response.headers["Server"] = "EMR-System"
    return response

# Request logging
@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.time()
    response = await call_next(request)
    elapsed = time.time() - start
    if elapsed > 5:
        print(f"⚠️ Slow: {request.method} {request.url.path} — {elapsed:.2f}s")
    return response

# Register routers
app.include_router(auth_router)
app.include_router(users_router)
app.include_router(patients_router)
app.include_router(analytics_router)
app.include_router(analytics_geo_router)
app.include_router(ai_insights.router)   # AI Insights (admin only)
app.include_router(ai_resources.router)  # Resource Allocation Analysis (admin only)
app.include_router(reports_router)
app.include_router(mr_router)
app.include_router(immun_router)
app.include_router(hp_router)
app.include_router(preg_router)
app.include_router(diseases_router)
app.include_router(audit_router)
app.include_router(inventory_router)
app.include_router(prescriptions_router)
app.include_router(websocket_router)
# Static files
BASE_DIR     = os.path.dirname(os.path.abspath(__file__))
FRONTEND_DIR = os.path.abspath(os.path.join(BASE_DIR, "..", "frontend"))

for folder in ["css", "js", "pages", "assets", "img", "vendor"]:
    path = os.path.join(FRONTEND_DIR, folder)
    if os.path.exists(path):
        app.mount(f"/frontend/{folder}", StaticFiles(directory=path), name=folder)

@app.get("/", include_in_schema=False)
async def root():
    p = os.path.join(FRONTEND_DIR, "pages", "login.html")
    return FileResponse(p) if os.path.exists(p) else {"status": "running"}

@app.get("/dashboard", include_in_schema=False)
async def dashboard():
    p = os.path.join(FRONTEND_DIR, "pages", "dashboard.html")
    return FileResponse(p) if os.path.exists(p) else {"status": "no dashboard"}

@app.get("/api/health", tags=["System"])
@limiter.limit("30/minute")
async def health(request: Request):
    return {"status": "ok", "barangay": get_barangay_name()}

@app.get("/api/barangay-info", tags=["System"])
async def barangay_info(current_user = Depends(get_current_user)):
    """Kunin ang barangay name ng kasalukuyang instance."""
    return {"barangay_name": get_barangay_name()}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app",
                host=os.getenv("APP_HOST", "0.0.0.0"),
                port=int(os.getenv("APP_PORT", "8000")),
                reload=os.getenv("DEBUG","False").lower()=="true")
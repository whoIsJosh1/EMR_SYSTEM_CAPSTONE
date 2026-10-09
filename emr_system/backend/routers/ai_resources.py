# ============================================================
# routers/ai_resources.py — Resource Allocation Analysis (ADMIN ONLY)
# Kasama ng routers/ai_insights.py (parehong prefix).
#
#   GET /api/ai-insights/resources?start_date=&end_date=
#       -> AGGREGATED na datos lang: buwanang paggamit ng bawat item,
#          current stock, buwanang bakuna, buwanang kaso, at bilang ng
#          reseta na nag-uugnay ng gamot sa sakit.
#
# Walang pangalan ng pasyente, patient ID, contact, o address na lumalabas.
# Ang pagsusuri (trend, seasonal, alerts) ay ginagawa sa
# frontend/js/resource-engine.js gamit lang ang mga datos na ito.
#
# DECISION-SUPPORT LANG: walang endpoint dito na bumibili, nag-o-order,
# nagdi-distribute, o nagbabago ng inventory. Puro pagbasa (SELECT).
# ============================================================
from datetime import date, datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import extract, func
from sqlalchemy.orm import Session

from database import get_db
from models.models import (
    Disease, DiseaseCase, Immunization, InventoryItem, InventoryTransaction,
    MedicalRecord, MedicalRecordPrescription, User,
)
from middleware.auth import require_admin

router = APIRouter(prefix="/api/ai-insights", tags=["AI Insights"])

MAX_RANGE_DAYS = 366 * 5


def _ym(year, month) -> str:
    return f"{int(year):04d}-{int(month):02d}"


@router.get("/resources")
def resource_data(
    start_date: date = Query(...),
    end_date:   date = Query(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),      # server-side admin check
):
    if start_date > end_date:
        raise HTTPException(status_code=400, detail="start_date must not be after end_date.")
    if (end_date - start_date).days > MAX_RANGE_DAYS:
        raise HTTPException(status_code=400, detail="Date range is too large (max 5 years).")

    start_dt = datetime.combine(start_date, datetime.min.time())
    end_dt   = datetime.combine(end_date + timedelta(days=1), datetime.min.time())

    # ── Mga item (kasalukuyang stock) ─────────────────────────
    items = [
        {
            "item_id": i.item_id, "name": i.item_name, "category": i.category,
            "unit": i.unit, "stock": int(i.current_stock or 0),
            "reorder": int(i.reorder_level or 0),
            "expires": i.expiration_date.isoformat() if i.expiration_date else None,
            "active": bool(i.is_active),
        }
        for i in db.query(InventoryItem).order_by(InventoryItem.item_name.asc()).all()
    ]

    # ── Buwanang paggamit: "dispense" lang ang bilang na paggamit ──
    y = extract("year", InventoryTransaction.created_at)
    m = extract("month", InventoryTransaction.created_at)
    usage = [
        {"month": _ym(yy, mm), "item_id": iid, "qty": int(q or 0), "n": int(n or 0)}
        for yy, mm, iid, q, n in (
            db.query(y, m, InventoryTransaction.item_id,
                     func.sum(InventoryTransaction.quantity), func.count(InventoryTransaction.transaction_id))
            .filter(InventoryTransaction.transaction_type == "dispense",
                    InventoryTransaction.created_at >= start_dt,
                    InventoryTransaction.created_at < end_dt)
            .group_by(y, m, InventoryTransaction.item_id)
            .all()
        )
    ]

    # ── Gaano katagal ang dispensing history (para sa data sufficiency) ──
    first_tx, last_tx = db.query(
        func.min(InventoryTransaction.created_at), func.max(InventoryTransaction.created_at)
    ).filter(InventoryTransaction.transaction_type == "dispense").one()

    # ── Mga bakuna na naibigay (Immunization records) ─────────
    iy = extract("year", Immunization.date_given)
    im = extract("month", Immunization.date_given)
    vaccines = [
        {"month": _ym(yy, mm), "vaccine": " ".join(str(v).split()), "doses": int(c or 0)}
        for yy, mm, v, c in (
            db.query(iy, im, Immunization.vaccine_name, func.count(Immunization.immunization_id))
            .filter(Immunization.date_given >= start_date, Immunization.date_given <= end_date)
            .group_by(iy, im, Immunization.vaccine_name)
            .all()
        )
    ]

    # ── Buwanang kaso ng sakit (hindi kasama ang "Rejected") ───
    cy = extract("year", DiseaseCase.date_recorded)
    cm = extract("month", DiseaseCase.date_recorded)
    cases = [
        {"month": _ym(yy, mm), "disease": " ".join(str(d).split()), "cases": int(c or 0)}
        for yy, mm, d, c in (
            db.query(cy, cm, Disease.disease_name, func.sum(DiseaseCase.number_of_cases))
            .select_from(DiseaseCase)
            .join(Disease, Disease.disease_id == DiseaseCase.disease_id)
            .filter(DiseaseCase.date_recorded >= start_date, DiseaseCase.date_recorded <= end_date,
                    DiseaseCase.case_status != "Rejected")
            .group_by(cy, cm, Disease.disease_name)
            .all()
        )
    ]

    # ── Ugnayan ng gamot at sakit: reseta → medical record → kaso ng sakit ──
    # Ang "link" ay mula lang sa AKTUWAL na reseta na na-dispense, na ang
    # medical record ay may naitalang kaso ng sakit sa parehong pasyente at petsa.
    # Bawat (reseta, sakit) ay isang beses lang binibilang.
    sub = (
        db.query(
            MedicalRecordPrescription.prescription_id.label("pid"),
            MedicalRecordPrescription.item_id.label("item_id"),
            MedicalRecordPrescription.dispensed_quantity.label("qty"),
            Disease.disease_name.label("disease"),
        )
        .select_from(MedicalRecordPrescription)
        .join(MedicalRecord, MedicalRecord.record_id == MedicalRecordPrescription.record_id)
        .join(DiseaseCase, (DiseaseCase.patient_id == MedicalRecord.patient_id)
              & (DiseaseCase.date_recorded == MedicalRecord.visit_date))
        .join(Disease, Disease.disease_id == DiseaseCase.disease_id)
        .filter(MedicalRecordPrescription.dispensed_quantity > 0,
                MedicalRecord.visit_date >= start_date, MedicalRecord.visit_date <= end_date,
                DiseaseCase.case_status != "Rejected")
        .distinct()
        .subquery()
    )
    links = [
        {"disease": " ".join(str(d).split()), "item_id": iid, "qty": int(q or 0), "rx": int(n or 0)}
        for d, iid, q, n in (
            db.query(sub.c.disease, sub.c.item_id, func.sum(sub.c.qty), func.count())
            .group_by(sub.c.disease, sub.c.item_id).all()
        )
    ]

    return {
        "start_date": start_date.isoformat(),
        "end_date":   end_date.isoformat(),
        "history": {
            "first": first_tx.date().isoformat() if first_tx else None,
            "last":  last_tx.date().isoformat()  if last_tx  else None,
        },
        "items": items, "usage": usage, "vaccines": vaccines,
        "cases": cases, "links": links,
    }

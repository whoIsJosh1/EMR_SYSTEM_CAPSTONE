# ============================================================
# routers/analytics_geo.py — Geographical Hotspots (Trend Analytics tab)
#
#   GET /api/analytics/purok-status
#       -> kaso per purok/sitio, may severity level, share %, at insight text
#
# Hiwalay na file ito para hindi magalaw ang routers/analytics.py.
# Ginagamit nito ang helper functions ng analytics.py
# (resolve_range, date_filter, extract_area).
# ============================================================
from typing import Optional
from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from database import get_db
from models.models import DiseaseCase, Disease, Patient
from middleware.auth import get_current_user
from routers.analytics import resolve_range, date_filter, extract_area

router = APIRouter(prefix="/api/analytics", tags=["Analytics"])

# ---------- SETTINGS (pwedeng baguhin) ----------
# Ang level ay kinukumpara sa PINAKA-MARAMING kaso sa isang purok sa napiling period.
HIGH_MIN, HIGH_RATIO = 10, 0.66      # High      = >=10 kaso AT >=66% ng top purok
MOD_MIN,  MOD_RATIO  = 5,  0.33      # Moderate  = >=5 kaso  AT >=33% ng top purok
INSIGHT_MIN_CASES    = 5             # kulang sa 5 kaso = walang insight (iwas maling konklusyon)
OTHER                = "Other areas" # address na walang purok/sitio (galing sa extract_area)
OTHER_LABEL          = "No purok recorded"   # ito ang ipapakita; hindi ito niraranggo bilang purok
# ------------------------------------------------


def _level(count: int, top: int) -> str:
    if count <= 0:
        return "safe"                # walang naitalang kaso
    ratio = count / top if top else 0
    if count >= HIGH_MIN and ratio >= HIGH_RATIO:
        return "high"
    if count >= MOD_MIN and ratio >= MOD_RATIO:
        return "moderate"
    return "low"


@router.get("/purok-status")
async def purok_status(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
    year:       Optional[int]  = Query(None),
    start_date: Optional[date] = Query(None),
    end_date:   Optional[date] = Query(None),
    disease:    Optional[str]  = Query(None, max_length=150),
):
    start, end = resolve_range(year, start_date, end_date)
    disease = (disease or "").strip() or None

    def only_disease(q):
        return q.filter(func.lower(Disease.disease_name) == disease.lower()) if disease else q

    # Kaso na may naka-link na pasyente → per address
    rows = only_disease(
        db.query(Patient.address, Disease.disease_name, func.sum(DiseaseCase.number_of_cases))
        .select_from(DiseaseCase)
        .join(Patient, Patient.patient_id == DiseaseCase.patient_id)
        .join(Disease, Disease.disease_id == DiseaseCase.disease_id)
        .filter(*date_filter(start, end), Patient.is_archived == False)   # noqa: E712
    ).group_by(Patient.address, Disease.disease_name).all()

    # Kaso na walang naka-link na pasyente -> mapupunta rin sa "No purok recorded"
    unlocated_rows = only_disease(
        db.query(Disease.disease_name, func.sum(DiseaseCase.number_of_cases))
        .select_from(DiseaseCase)
        .join(Disease, Disease.disease_id == DiseaseCase.disease_id)
        .filter(*date_filter(start, end), DiseaseCase.patient_id.is_(None))
    ).group_by(Disease.disease_name).all()
    unlocated = sum(int(n or 0) for _, n in unlocated_rows)

    per_area = {}
    for address, dname, n in rows:
        a = per_area.setdefault(extract_area(address), {"count": 0, "diseases": {}})
        a["count"] += int(n or 0)
        a["diseases"][dname] = a["diseases"].get(dname, 0) + int(n or 0)

    for dname, n in unlocated_rows:
        a = per_area.setdefault(OTHER, {"count": 0, "diseases": {}})
        a["count"] += int(n or 0)
        a["diseases"][dname] = a["diseases"].get(dname, 0) + int(n or 0)

    # Isama ang mga purok na may pasyente pero 0 kaso (lalabas na "Safe")
    for (addr,) in db.query(Patient.address).filter(Patient.is_archived == False).distinct().all():  # noqa: E712
        name = extract_area(addr)
        if name != OTHER:
            per_area.setdefault(name, {"count": 0, "diseases": {}})

    named_counts = [v["count"] for k, v in per_area.items() if k != OTHER]
    top   = max(named_counts, default=0)
    total = sum(v["count"] for v in per_area.values())

    areas = []
    for name, v in per_area.items():
        is_other = name == OTHER
        dz = sorted(v["diseases"].items(), key=lambda kv: -kv[1])[:3]
        areas.append({
            "name": OTHER_LABEL if is_other else name, "count": v["count"], "is_other": is_other,
            "share": round(v["count"] / total * 100, 1) if total else 0,
            "level": "other" if is_other else _level(v["count"], top),
            "diseases": [{"name": n, "count": c} for n, c in dz],
        })
    areas.sort(key=lambda a: (-a["count"], a["name"]))

    # System insight (batay lang sa naitalang datos; walang hula)
    named = [a for a in areas if not a["is_other"]]
    lead  = named[0] if named and named[0]["count"] > 0 else None
    scope = f" for {disease}" if disease else ""
    if total < INSIGHT_MIN_CASES or not lead:
        insight = f"Not enough located cases{scope} in this period to point to a pattern yet."
    else:
        insight = (f"{lead['name']} accounts for {lead['share']}% of the cases{scope} recorded "
                   f"in this period ({lead['count']} of {total}).")
        if not disease and lead["diseases"]:
            insight += f" The most common there is {lead['diseases'][0]['name']}."

    return {
        "start_date": start.isoformat(), "end_date": end.isoformat(),
        "disease": disease, "total": int(total), "unlocated": int(unlocated),
        "areas": areas, "insight": insight,
        "rules": {"high_min": HIGH_MIN, "high_ratio": int(HIGH_RATIO * 100),
                  "mod_min": MOD_MIN, "mod_ratio": int(MOD_RATIO * 100)},
    }
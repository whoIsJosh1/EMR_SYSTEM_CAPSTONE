# ============================================================
# routers/analytics.py — Disease Trend Analytics
# Single-barangay — no barangay FK or dropdown needed
# All data is from the current database instance
# ============================================================

from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func, extract, or_
from typing import Optional
from datetime import datetime, date, timedelta
import pandas as pd

from database import get_db, get_barangay_name
from models.models import DiseaseCase, Disease, Patient, MedicalRecord, Immunization, User
from middleware.auth import get_current_user

router = APIRouter(prefix="/api/analytics", tags=["Analytics"])

# Color palette for charts
COLORS = [
    '#0b1f4b','#1a6fad','#1a8a5e','#e74c3c','#f39c12',
    '#8e44ad','#1abc9c','#e67e22','#3498db','#2ecc71',
    '#e91e63','#ff5722','#607d8b','#795548','#00bcd4'
]

MONTHS_SHORT = ['Jan','Feb','Mar','Apr','May','Jun',
                'Jul','Aug','Sep','Oct','Nov','Dec']
MAX_RANGE_MONTHS = 120   # 10 years — proteksyon laban sa sobrang laking range


def resolve_range(year: Optional[int], start_date: Optional[date], end_date: Optional[date]):
    """
    Gawing (start, end) ang mga filter parameters.
    - Kapag may start_date/end_date  -> gamitin ang date range.
    - Kapag wala, gamitin ang `year` (o current year) -> Jan 1 hanggang Dec 31.
      (Backward-compatible sa mga lumang tawag na ?year=2026 lang.)
    """
    today = date.today()
    if start_date or end_date:
        end   = end_date or today
        start = start_date or date(end.year, 1, 1)
    else:
        y = year or today.year
        start, end = date(y, 1, 1), date(y, 12, 31)

    if start > end:
        raise HTTPException(status_code=400, detail="start_date must not be after end_date.")
    if (end.year - start.year) * 12 + (end.month - start.month) + 1 > MAX_RANGE_MONTHS:
        raise HTTPException(status_code=400, detail="Date range is too large (max 10 years).")
    return start, end


def date_filter(start: date, end: date):
    """
    Inclusive na filter sa DiseaseCase.date_recorded.
    Ang `< end + 1 day` ay gumagana kahit DATE o DATETIME ang column.
    """
    return (
        DiseaseCase.date_recorded >= start,
        DiseaseCase.date_recorded <  end + timedelta(days=1),
    )


def month_buckets(start: date, end: date):
    """List ng (year, month) mula start hanggang end, inclusive."""
    buckets, y, m = [], start.year, start.month
    while (y, m) <= (end.year, end.month):
        buckets.append((y, m))
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return buckets


def bucket_labels(buckets):
    """'Jan' kung iisang taon lang ang range; 'Jan 2025' kung lampas isang taon."""
    multi_year = len({y for y, _ in buckets}) > 1
    return [f"{MONTHS_SHORT[m-1]} {y}" if multi_year else MONTHS_SHORT[m-1]
            for y, m in buckets]



@router.get("/dashboard-summary")
async def dashboard_summary(
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """
    Lahat ng summary numbers para sa dashboard.
    Isang API call lang — para sa stat cards.
    """
    now           = datetime.now()
    # NOTE: func.count(<primary key>) imbes na query(Model).count().
    # Ang query(Model).count() ay nagse-select ng LAHAT ng column ng model, kaya
    # kapag may column sa models.py na wala pa sa database (hal. follow_up_* sa
    # medical_records) -> "Unknown column" -> 500. Ang func.count(PK) ay hindi apektado.
    total_patients = db.query(func.count(Patient.patient_id)).filter(
        Patient.is_archived == False
    ).scalar() or 0
    total_records  = db.query(func.count(MedicalRecord.record_id)).scalar() or 0
    total_immun    = db.query(func.count(Immunization.immunization_id)).scalar() or 0
    total_female   = db.query(func.count(Patient.patient_id)).filter(
        Patient.is_archived == False, Patient.sex == "Female"
    ).scalar() or 0

    # Cases this month
    cases_month = db.query(func.sum(DiseaseCase.number_of_cases)).filter(
        extract('month', DiseaseCase.date_recorded) == now.month,
        extract('year',  DiseaseCase.date_recorded) == now.year
    ).scalar() or 0

    # Top disease this month
    top = db.query(
        Disease.disease_name,
        func.sum(DiseaseCase.number_of_cases).label('total')
    ).join(DiseaseCase).filter(
        extract('month', DiseaseCase.date_recorded) == now.month,
        extract('year',  DiseaseCase.date_recorded) == now.year
    ).group_by(Disease.disease_name).order_by(
        func.sum(DiseaseCase.number_of_cases).desc()
    ).first()

    # BHW stats (admin only)
    total_bhw  = db.query(func.count(User.user_id)).filter(User.role == "bhw").scalar() or 0
    active_bhw = db.query(func.count(User.user_id)).filter(
        User.role == "bhw", User.status == "active"
    ).scalar() or 0

    return {
        "barangay_name":         get_barangay_name(),
        "total_patients":        total_patients,
        "total_medical_records": total_records,
        "total_immunizations":   total_immun,
        "total_female_patients": total_female,
        "cases_this_month":      int(cases_month),
        "top_disease_this_month":top.disease_name if top else "N/A",
        "total_bhw":             total_bhw,
        "active_bhw":            active_bhw,
        "current_month":         now.strftime("%B %Y")
    }

@router.get("/disease-trends")
async def disease_trends(
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
    year:       Optional[int]  = Query(None),
    start_date: Optional[date] = Query(None),
    end_date:   Optional[date] = Query(None)
):
    """
    Monthly disease trend data para sa Chart.js line chart.
    Ibinabalik ang isang dataset per disease, isang point per buwan
    sa loob ng napiling date range (o buong taon kung `year` lang ang ipinadala).
    """
    start, end = resolve_range(year, start_date, end_date)
    buckets    = month_buckets(start, end)
    labels     = bucket_labels(buckets)
    meta       = {"year": start.year, "start_date": start.isoformat(), "end_date": end.isoformat()}

    rows = db.query(
        DiseaseCase.date_recorded,
        DiseaseCase.number_of_cases,
        Disease.disease_name
    ).join(Disease).filter(*date_filter(start, end)).all()

    if not rows:
        return {"labels": [], "datasets": [], **meta}

    df = pd.DataFrame(rows, columns=['date_recorded','number_of_cases','disease_name'])
    dt = pd.to_datetime(df['date_recorded'])
    df['yr']    = dt.dt.year
    df['month'] = dt.dt.month

    diseases = df['disease_name'].unique().tolist()

    datasets = []
    for idx, disease in enumerate(diseases):
        sub    = df[df['disease_name'] == disease]
        totals = []
        for (y, m) in buckets:
            row = sub[(sub['yr'] == y) & (sub['month'] == m)]
            totals.append(int(row['number_of_cases'].sum()) if not row.empty else 0)

        color = COLORS[idx % len(COLORS)]
        datasets.append({
            "label":            disease,
            "data":             totals,
            "borderColor":      color,
            "backgroundColor":  color + "25",
            "tension":          0.4,
            "fill":             True,
            "pointRadius":      5,
            "pointHoverRadius": 7,
            "borderWidth":      2.5
        })

    return {"labels": labels, "datasets": datasets, **meta}


@router.get("/top-diseases")
async def top_diseases(
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
    year:       Optional[int]  = Query(None),
    start_date: Optional[date] = Query(None),
    end_date:   Optional[date] = Query(None),
    limit: int                 = Query(8, ge=1, le=15)
):
    """
    Top N diseases by case count para sa doughnut chart.
    """
    start, end = resolve_range(year, start_date, end_date)

    rows = db.query(
        Disease.disease_name,
        func.sum(DiseaseCase.number_of_cases).label('total')
    ).join(DiseaseCase).filter(
        *date_filter(start, end)
    ).group_by(Disease.disease_name).order_by(
        func.sum(DiseaseCase.number_of_cases).desc()
    ).limit(limit).all()

    return {
        "labels":          [r.disease_name for r in rows],
        "data":            [int(r.total)   for r in rows],
        "backgroundColor": COLORS[:len(rows)]
    }


@router.get("/monthly-cases")
async def monthly_cases(
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
    year:       Optional[int]  = Query(None),
    start_date: Optional[date] = Query(None),
    end_date:   Optional[date] = Query(None)
):
    """
    Total cases per month para sa bar chart (sa loob ng napiling range).
    """
    start, end = resolve_range(year, start_date, end_date)
    buckets    = month_buckets(start, end)

    rows = db.query(
        extract('year',  DiseaseCase.date_recorded).label('yr'),
        extract('month', DiseaseCase.date_recorded).label('month'),
        func.sum(DiseaseCase.number_of_cases).label('total')
    ).filter(
        *date_filter(start, end)
    ).group_by('yr', 'month').order_by('yr', 'month').all()

    by_month = {(int(r.yr), int(r.month)): int(r.total) for r in rows}
    totals   = [by_month.get(b, 0) for b in buckets]

    return {
        "labels":          bucket_labels(buckets),
        "data":            totals,
        "backgroundColor": [COLORS[0]] * len(buckets)
    }


@router.get("/age-distribution")
async def age_distribution(
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
    year: Optional[int] = Query(None)
):
    """
    Distribusyon ng edad ng mga pasyente na may medical record sa napiling taon.
    Edad = edad nila hanggang Dec 31 ng taong iyon (o hanggang ngayon kung current year).
    """
    y   = year or date.today().year
    ref = pd.Timestamp(min(date(y, 12, 31), date.today()))

    # Mga patient_id na may kahit isang medical record sa taong iyon (distinct na)
    patient_ids = db.query(MedicalRecord.patient_id).filter(
        extract('year', MedicalRecord.created_at) == y     # TODO: palitan kung iba ang pangalan ng column
    ).distinct()

    rows = db.query(Patient.birthdate, Patient.sex).filter(
        Patient.is_archived == False,
        Patient.patient_id.in_(patient_ids)                   # TODO: palitan kung iba ang PK ng Patient
    ).all()

    if not rows:
        return {"labels": [], "male": [], "female": []}

    df = pd.DataFrame(rows, columns=['birthdate', 'sex'])
    bd = pd.to_datetime(df['birthdate'])

    # Eksaktong edad as of `ref` (hindi days // 365)
    had_birthday = (bd.dt.month < ref.month) | ((bd.dt.month == ref.month) & (bd.dt.day <= ref.day))
    df['age'] = ref.year - bd.dt.year - (~had_birthday).astype(int)

    bins   = [-1, 4, 12, 17, 35, 59, 150]    # -1 para kasama ang edad 0
    labels = ['0-4', '5-12', '13-17', '18-35', '36-59', '60+']
    df['age_group'] = pd.cut(df['age'], bins=bins, labels=labels, right=True)

    male   = df[df['sex'] == 'Male'].groupby('age_group', observed=True).size()
    female = df[df['sex'] == 'Female'].groupby('age_group', observed=True).size()

    return {
        "labels": labels,
        "male":   [int(male.get(l, 0))   for l in labels],
        "female": [int(female.get(l, 0)) for l in labels]
    }


@router.get("/surveillance")
async def surveillance(
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
    year:       Optional[int]  = Query(None),
    start_date: Optional[date] = Query(None),
    end_date:   Optional[date] = Query(None)
):
    """
    Disease surveillance table — case counts per disease.
    Para sa Surveillance section ng dashboard.
    Tumatanggap ng start_date/end_date (YYYY-MM-DD) o ng lumang `year`.
    """
    start, end = resolve_range(year, start_date, end_date)

    rows = db.query(
        Disease.disease_name,
        Disease.category,
        func.sum(DiseaseCase.number_of_cases).label('total')
    ).join(DiseaseCase).filter(
        *date_filter(start, end)
    ).group_by(Disease.disease_name, Disease.category).order_by(
        func.sum(DiseaseCase.number_of_cases).desc()
    ).all()

    grand_total = sum(r.total for r in rows) if rows else 0

    return {
        "year":        start.year,
        "start_date":  start.isoformat(),
        "end_date":    end.isoformat(),
        "grand_total": int(grand_total),
        "diseases": [
            {
                "disease_name": r.disease_name,
                "category":     r.category,
                "total":        int(r.total),
                "pct":          round(r.total / grand_total * 100, 1) if grand_total else 0
            }
            for r in rows
        ]
    }

# ============================================================
# LIVE DASHBOARD — Alerts, Recent Consultations, Hotspot Map
# ------------------------------------------------------------
#   /api/analytics/alerts               -> Emergency & Operational Alert
#   /api/analytics/recent-consultations -> Recent Consultation (Real-Time Feed)
#   /api/analytics/hotspots             -> Geographical Hotspot Map
# Naka-base na sa totoong models.py (walang hulaan ng column).
# ============================================================
import re
from models.models import InventoryItem, Pregnancy

# ---------- SETTINGS (pwede mong baguhin) ----------
ALERT_WINDOW_DAYS = 7     # huling 7 araw vs 7 araw bago nito
SURGE_MIN_CASES   = 5     # minimum na kaso bago ituring na "surge"
SURGE_CRITICAL    = 15    # ganito karami = critical kahit hindi notifiable
EXPIRY_WARN_DAYS  = 30
DUE_SOON_DAYS     = 14    # pregnancy due date na malapit na

# Center ng mapa kung walang pin (PALITAN ng actual lat/lng ng barangay mo)
HOTSPOT_CENTER = [14.7156, 120.9661]

# Walang latitude/longitude ang Patient, kaya dito mo ilalagay ang coordinates
# ng bawat purok/sitio (lowercase ang key). Kapag may laman ito, lalabas ang
# mga pin sa mapa; kapag wala, ranking list ang ipapakita.
# Halimbawa:
#   "purok 1": [14.7012, 120.9811],
#   "purok 2": [14.7025, 120.9794],
PUROK_COORDS: dict = {
    # "purok 1":        [14.7223, 120.9676],
    # "purok 2":        [14.7188, 120.9648],
    # "purok 3":        [14.7175, 120.9721],
    # "purok 4":        [14.7129, 120.9629],
    # "purok 5":        [14.7125, 120.9738],
    # "purok 6":        [14.7092, 120.9639],
    # "purok 7":        [14.7084, 120.9704],
    # "purok 8":        [14.7055, 120.9777],
    # "block 5":        [14.7042, 120.9725],
    # "sitio maligaya": [14.7113, 120.9665],
}
# ---------------------------------------------------


def _age_from(birthdate, ref=None):
    if not birthdate:
        return None
    ref = ref or date.today()
    bd = birthdate.date() if isinstance(birthdate, datetime) else birthdate
    return ref.year - bd.year - ((ref.month, ref.day) < (bd.month, bd.day))


_AREA_RE = re.compile(r"\b(purok|sitio|zone|phase|block|blk)\s*[-#.]?\s*([0-9]+[a-z]?|[a-z]+)\b", re.I)

def extract_area(address: Optional[str]) -> str:
    """Hanapin ang purok/sitio/zone sa free-text na address. Hal. '12 Rizal St., Purok 3' -> 'Purok 3'."""
    if not address:
        return "Other areas"
    m = _AREA_RE.search(address)
    if not m:
        return "Other areas"
    kind, val = m.group(1).title(), m.group(2)
    kind = "Block" if kind == "Blk" else kind
    val  = val.title() if (val.isalpha() and len(val) > 3) else val.upper()
    return f"{kind} {val}"


# ------------------------------------------------------------
# 1) ALERTS
# ------------------------------------------------------------
def _outbreak_alerts(db: Session):
    today      = date.today()
    cur_start  = today - timedelta(days=ALERT_WINDOW_DAYS - 1)
    prev_start = cur_start - timedelta(days=ALERT_WINDOW_DAYS)
    prev_end   = cur_start - timedelta(days=1)

    def totals(s, e):
        rows = db.query(
            Disease.disease_name, Disease.is_notifiable,
            func.sum(DiseaseCase.number_of_cases)
        ).join(DiseaseCase).filter(*date_filter(s, e)).group_by(
            Disease.disease_name, Disease.is_notifiable).all()
        return {n: (int(t or 0), bool(nf)) for n, nf, t in rows}

    cur, prev = totals(cur_start, today), totals(prev_start, prev_end)
    out = []
    for name, (c, notifiable) in cur.items():
        if c <= 0:
            continue
        p     = prev.get(name, (0, False))[0]
        surge = c >= SURGE_MIN_CASES and c >= 2 * p

        if surge and (notifiable or c >= SURGE_CRITICAL):
            level = "critical"
        elif surge:
            level = "warning"
        elif notifiable:
            level = "critical" if c >= 3 else "warning"
        else:
            continue

        change = "new this week" if p == 0 else f"{(c - p) / p * 100:+.0f}% vs last week"
        tag    = " (notifiable)" if notifiable else ""
        out.append({
            "id": f"outbreak-{name}", "level": level, "category": "outbreak",
            "title":   f"{name}{tag} {'surge' if surge else 'reported'}",
            "message": f"{c} case(s) in the last {ALERT_WINDOW_DAYS} days ({change}; previous week: {p}).",
            "count":   c, "link": "#analytics",
        })
    return out


def _inventory_alerts(db: Session):
    today = date.today()
    items = db.query(
        InventoryItem.item_name, InventoryItem.current_stock,
        InventoryItem.reorder_level, InventoryItem.expiration_date
    ).filter(InventoryItem.is_active == True).all()

    out_of_stock, low, expired, expiring = [], [], [], []
    for name, stock, reorder, exp in items:
        stock = stock or 0
        if stock <= 0:
            out_of_stock.append(name)
        elif stock <= (reorder or 0):
            low.append(name)
        if exp:
            if exp < today:
                expired.append(name)
            elif (exp - today).days <= EXPIRY_WARN_DAYS:
                expiring.append(name)

    def sample(names):
        s = ", ".join(names[:3])
        return s + (f" +{len(names) - 3} more" if len(names) > 3 else "")

    link = "/frontend/pages/inventory.html"
    out = []
    if out_of_stock:
        out.append({"id": "inv-out", "level": "critical", "category": "inventory",
                    "title": f"{len(out_of_stock)} item(s) out of stock",
                    "message": sample(out_of_stock), "count": len(out_of_stock), "link": link})
    if expired:
        out.append({"id": "inv-expired", "level": "critical", "category": "inventory",
                    "title": f"{len(expired)} expired item(s) still active",
                    "message": sample(expired), "count": len(expired), "link": link})
    if low:
        out.append({"id": "inv-low", "level": "warning", "category": "inventory",
                    "title": f"{len(low)} item(s) at or below reorder level",
                    "message": sample(low), "count": len(low), "link": link})
    if expiring:
        out.append({"id": "inv-expiring", "level": "warning", "category": "inventory",
                    "title": f"{len(expiring)} item(s) expiring within {EXPIRY_WARN_DAYS} days",
                    "message": sample(expiring), "count": len(expiring), "link": link})
    return out


def _followup_alerts(db: Session):
    """Overdue / due-today na follow-up check-ups (galing sa medical_records)."""
    today = date.today()
    base  = db.query(func.count(MedicalRecord.record_id)).join(
        Patient, Patient.patient_id == MedicalRecord.patient_id
    ).filter(Patient.is_archived == False, MedicalRecord.follow_up_required == True)

    overdue   = base.filter(MedicalRecord.follow_up_date < today).scalar() or 0
    due_today = base.filter(MedicalRecord.follow_up_date == today).scalar() or 0
    out = []
    if overdue:
        out.append({"id": "fu-overdue", "level": "warning", "category": "followup",
                    "title": f"{overdue} overdue follow-up check-up(s)",
                    "message": "Patients flagged for follow-up whose date has already passed.",
                    "count": overdue, "link": "/frontend/pages/patients.html"})
    if due_today:
        out.append({"id": "fu-today", "level": "info", "category": "followup",
                    "title": f"{due_today} follow-up(s) scheduled today",
                    "message": "Expect these patients at the health center today.",
                    "count": due_today, "link": "/frontend/pages/patients.html"})
    return out


def _pregnancy_alerts(db: Session):
    """Buntis na malapit na ang due date."""
    today = date.today()
    n = db.query(func.count(Pregnancy.pregnancy_id)).filter(
        Pregnancy.status == "Pregnant",
        Pregnancy.expected_due_date != None,
        Pregnancy.expected_due_date >= today,
        Pregnancy.expected_due_date <= today + timedelta(days=DUE_SOON_DAYS)
    ).scalar() or 0
    if not n:
        return []
    return [{"id": "preg-due", "level": "info", "category": "pregnancy",
             "title": f"{n} pregnancy due within {DUE_SOON_DAYS} days",
             "message": "Prepare delivery referral and birth-plan coordination.",
             "count": n, "link": "/frontend/pages/patients.html"}]


@router.get("/alerts")
async def alerts(
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Emergency (outbreak) + Operational (inventory, follow-up, pregnancy) alerts."""
    items = []
    for source in (_outbreak_alerts, _inventory_alerts, _followup_alerts, _pregnancy_alerts):
        try:
            items += source(db)
        except Exception as e:           # isang source lang ang pumalpak -> tuloy pa rin ang iba
            db.rollback()
            print(f"[alerts] {source.__name__} skipped: {e}")

    rank = {"critical": 0, "warning": 1, "info": 2}
    items.sort(key=lambda a: (rank.get(a["level"], 3), -a.get("count", 0)))
    return {
        "alerts": items,
        "counts": {lv: sum(1 for a in items if a["level"] == lv) for lv in rank},
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }


# ------------------------------------------------------------
# 2) RECENT CONSULTATIONS (live feed)
# ------------------------------------------------------------
@router.get("/recent-consultations")
async def recent_consultations(
    limit: int = Query(8, ge=1, le=30),
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    rows = db.query(
        MedicalRecord.record_id, MedicalRecord.patient_id, MedicalRecord.created_at,
        MedicalRecord.visit_date, MedicalRecord.diagnosis, MedicalRecord.chief_complaint,
        Patient.first_name, Patient.middle_name, Patient.last_name,
        Patient.birthdate, Patient.sex
    ).join(Patient, Patient.patient_id == MedicalRecord.patient_id
    ).filter(Patient.is_archived == False
    ).order_by(MedicalRecord.created_at.desc(), MedicalRecord.record_id.desc()
    ).limit(limit).all()

    items = []
    for r in rows:
        stamp = r.created_at or (datetime.combine(r.visit_date, datetime.min.time()) if r.visit_date else None)
        text  = (r.diagnosis or r.chief_complaint or "Consultation").strip()
        items.append({
            "record_id":    r.record_id,
            "patient_id":   r.patient_id,
            "patient_name": " ".join(p for p in (r.first_name, r.middle_name, r.last_name) if p),
            "age":          _age_from(r.birthdate),
            "sex":          r.sex,
            "diagnosis":    text if len(text) <= 80 else text[:77] + "…",
            "created_at":   stamp.isoformat() if stamp else None,
        })
    return {"items": items}


# ------------------------------------------------------------
# 3) HOTSPOT MAP — disease cases per purok/area
# ------------------------------------------------------------
@router.get("/hotspots")
async def hotspots(
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
    year:       Optional[int]  = Query(None),
    start_date: Optional[date] = Query(None),
    end_date:   Optional[date] = Query(None)
):
    """
    Disease cases na may naka-link na pasyente, naka-group per purok/sitio
    (kinukuha sa Patient.address). May pin sa mapa kung nasa PUROK_COORDS ang area;
    kung wala, ranking list ("list" mode) ang ibabalik.
    """
    start, end = resolve_range(year, start_date, end_date)
    base = {"start_date": start.isoformat(), "end_date": end.isoformat(), "center": HOTSPOT_CENTER}

    rows = db.query(
        Patient.address, Disease.disease_name, func.sum(DiseaseCase.number_of_cases)
    ).select_from(DiseaseCase
    ).join(Patient, Patient.patient_id == DiseaseCase.patient_id
    ).join(Disease, Disease.disease_id == DiseaseCase.disease_id
    ).filter(*date_filter(start, end), Patient.is_archived == False
    ).group_by(Patient.address, Disease.disease_name).all()

    unlocated = db.query(func.sum(DiseaseCase.number_of_cases)).filter(
        *date_filter(start, end), DiseaseCase.patient_id.is_(None)
    ).scalar() or 0

    per_area = {}                     # area -> {"count": n, "diseases": {name: n}}
    for address, dname, n in rows:
        a = per_area.setdefault(extract_area(address), {"count": 0, "diseases": {}})
        a["count"] += int(n or 0)
        a["diseases"][dname] = a["diseases"].get(dname, 0) + int(n or 0)

    areas = []
    for name, v in sorted(per_area.items(), key=lambda kv: -kv[1]["count"])[:30]:
        coords = PUROK_COORDS.get(name.lower())
        top    = max(v["diseases"].items(), key=lambda kv: kv[1])[0] if v["diseases"] else None
        areas.append({
            "name": name, "count": v["count"],
            "lat": coords[0] if coords else None,
            "lng": coords[1] if coords else None,
            "top_disease": top,
        })

    mode = "map" if any(a["lat"] is not None for a in areas) else ("list" if areas else "none")
    return {**base, "mode": mode, "areas": areas,
            "total": sum(a["count"] for a in areas), "unlocated": int(unlocated)}


# ============================================================
# SURVEILLANCE HUB — Live Disease Guard
# ------------------------------------------------------------
#   GET  /api/analytics/surveillance-hub        -> cards + validation queue + weekly chart
#   POST /api/analytics/cases/{case_id}/review  -> Verify / Reject (nurse, midwife, doctor)
# Morbidity week = Linggo hanggang Sabado (Sunday start).
# Threshold ay FLEXIBLE: "computed" (mean + k x SD ng nakaraang weeks) o "fixed" (manual na bilang).
# ============================================================
import math
import statistics
from collections import defaultdict
from fastapi import Body

HUB_DISPLAY_WEEKS = 12                     # ilang linggo ang ipapakita sa chart
HUB_PENDING       = ("Suspected", "Probable")
HUB_REVIEW_ROLES  = ("nurse", "midwife", "doctor")


def _morb_week_start(d: date) -> date:
    return d - timedelta(days=(d.weekday() + 1) % 7)       # Sunday


def _morb_week_no(week_start: date) -> int:
    return (week_start + timedelta(days=1)).isocalendar()[1]


def _hub_thresholds(base_counts, cfg):
    """(alert, epidemic) -> kapag umabot o lumagpas ang cases sa isang linggo, flagged."""
    if cfg["method"] == "fixed":
        a, e = cfg["alert_fixed"], cfg["epidemic_fixed"]
    else:
        n    = len(base_counts)
        mean = sum(base_counts) / n if n else 0
        sd   = statistics.stdev(base_counts) if n > 1 else 0
        a    = max(cfg["min_cases"], math.ceil(mean + cfg["alert_sd"] * sd))
        e    = max(cfg["min_cases"], math.ceil(mean + cfg["epidemic_sd"] * sd))
    return a, max(a, e)


@router.get("/surveillance-hub")
async def surveillance_hub(
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
    method:         str   = Query("computed"),
    baseline_weeks: int   = Query(8,   ge=3, le=52),
    alert_sd:       float = Query(1.0, ge=0, le=5),
    epidemic_sd:    float = Query(2.0, ge=0, le=10),
    min_cases:      int   = Query(3,   ge=1, le=100),
    alert_fixed:    int   = Query(5,   ge=1, le=10000),
    epidemic_fixed: int   = Query(10,  ge=1, le=10000),
):
    if method not in ("computed", "fixed"):
        raise HTTPException(status_code=400, detail="method must be 'computed' or 'fixed'.")
    cfg = dict(method=method, alert_sd=alert_sd, epidemic_sd=epidemic_sd, min_cases=min_cases,
               alert_fixed=alert_fixed, epidemic_fixed=epidemic_fixed)

    today     = date.today()
    cur_start = _morb_week_start(today)
    span      = max(HUB_DISPLAY_WEEKS - 1, baseline_weeks)
    first     = cur_start - timedelta(weeks=span)

    # ---- case counts per (purok, disease, week) — hindi kasama ang Rejected ----
    rows = db.query(
        DiseaseCase.date_recorded, Disease.disease_name, Patient.address,
        func.sum(DiseaseCase.number_of_cases)
    ).select_from(DiseaseCase
    ).join(Disease, Disease.disease_id == DiseaseCase.disease_id
    ).outerjoin(Patient, Patient.patient_id == DiseaseCase.patient_id
    ).filter(DiseaseCase.date_recorded >= first, DiseaseCase.date_recorded <= today,
             DiseaseCase.case_status != "Rejected"
    ).group_by(DiseaseCase.date_recorded, Disease.disease_name, Patient.address).all()

    by_disease = defaultdict(lambda: defaultdict(int))     # disease -> week -> n
    by_pair    = defaultdict(lambda: defaultdict(int))     # (purok, disease) -> week -> n
    for d, dname, addr, n in rows:
        d = d.date() if isinstance(d, datetime) else d
        ws, n = _morb_week_start(d), int(n or 0)
        by_disease[dname][ws] += n
        by_pair[(extract_area(addr), dname)][ws] += n

    base_weeks = [cur_start - timedelta(weeks=i) for i in range(1, baseline_weeks + 1)]
    show_weeks = [cur_start - timedelta(weeks=i) for i in range(HUB_DISPLAY_WEEKS - 1, -1, -1)]

    # ---- weekly chart per disease ----
    diseases = {}
    for dname, wk in sorted(by_disease.items()):
        a, e = _hub_thresholds([wk.get(w, 0) for w in base_weeks], cfg)
        diseases[dname] = {"cases": [wk.get(w, 0) for w in show_weeks],
                           "alert_level": a, "epidemic_limit": e}

    # ---- outbreak sectors: purok na lumagpas sa threshold ngayong linggo ----
    flagged = []
    for (purok, dname), wk in by_pair.items():
        c = wk.get(cur_start, 0)
        if c <= 0 or purok == "Other areas":
            continue
        a, e = _hub_thresholds([wk.get(w, 0) for w in base_weeks], cfg)
        if c >= a:
            flagged.append({"purok": purok, "disease": dname, "cases": c,
                            "alert_level": a, "epidemic_limit": e,
                            "level": "epidemic" if c >= e else "alert"})
    flagged.sort(key=lambda x: (x["level"] != "epidemic", -x["cases"]))

    # ---- cards ----
    morbidity = sum(wk.get(cur_start, 0) for wk in by_disease.values())
    active_alerts = db.query(func.count(DiseaseCase.case_id)).join(
        Disease, Disease.disease_id == DiseaseCase.disease_id
    ).filter(Disease.is_notifiable == True, DiseaseCase.case_status.in_(HUB_PENDING),
             DiseaseCase.date_recorded >= cur_start, DiseaseCase.date_recorded <= today).scalar() or 0

    # ---- validation queue ----
    qrows = db.query(
        DiseaseCase.case_id, DiseaseCase.case_status, DiseaseCase.date_recorded, DiseaseCase.escalated_at,
        Disease.disease_name, Disease.icd_code, Patient.address
    ).join(Disease, Disease.disease_id == DiseaseCase.disease_id
    ).outerjoin(Patient, Patient.patient_id == DiseaseCase.patient_id
    ).filter(DiseaseCase.case_status.in_(HUB_PENDING),
             or_(Patient.patient_id.is_(None), Patient.is_archived == False)
    ).order_by(DiseaseCase.escalated_at.is_(None), DiseaseCase.date_recorded.desc(), DiseaseCase.case_id.desc()).limit(300).all()

    queue = [{
        "case_id": r.case_id,
        "code":    f"{''.join(ch for ch in r.disease_name if ch.isalpha())[:3].upper()}-{r.case_id:03d}",
        "disease": r.disease_name, "icd": r.icd_code,
        "purok":   extract_area(r.address), "status": r.case_status,
        "date":    r.date_recorded.isoformat() if r.date_recorded else None,
        "escalated": r.escalated_at is not None,
    } for r in qrows]

    role = current_user.get("role") if isinstance(current_user, dict) else getattr(current_user, "role", None)
    return {
        "week": {"number": _morb_week_no(cur_start), "start": cur_start.isoformat(),
                 "end": (cur_start + timedelta(days=6)).isoformat()},
        "alerts": int(active_alerts), "morbidity_cases": int(morbidity),
        "outbreak_sectors": len({f["purok"] for f in flagged}), "outbreak_detail": flagged,
        "pidsr": {"status": "Not configured"},        # wala pang DOH PIDSR integration
        "can_review": role in HUB_REVIEW_ROLES, "role": role,
        "queue": queue,
        "weekly": {"labels": [f"Wk {_morb_week_no(w)}" for w in show_weeks], "diseases": diseases},
    }


# ------------------------------------------------------------
# CASE REVIEW — modal ng nurse/midwife (draft, escalate) at doctor (confirm, reject)
# Ang system ay gabay lang; ang doctor ang huling nagpapasya.
# ------------------------------------------------------------
from sqlalchemy import text as sql_text

LAB_RESULTS = ("Not done", "Pending", "Positive", "Negative")
REVIEW_ACTIONS = {"nurse": ("draft", "escalate"), "midwife": ("draft", "escalate"),
                  "doctor": ("draft", "confirm", "reject")}


def ensure_case_schema(engine):
    """Safe i-run kahit ilang beses: nagdadagdag ng columns sa disease_cases at gumagawa ng case_reviews."""
    with engine.begin() as conn:
        have = {r[0] for r in conn.execute(sql_text(
            "SELECT COLUMN_NAME FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'disease_cases'"))}
        cols = {
            "case_status":  "case_status ENUM('Suspected','Probable','Confirmed','Rejected') NOT NULL DEFAULT 'Confirmed'",
            "verified_by":  "verified_by INT NULL", "verified_at":  "verified_at DATETIME NULL",
            "escalated_by": "escalated_by INT NULL", "escalated_at": "escalated_at DATETIME NULL",
        }
        for name, ddl in cols.items():
            if name not in have:
                conn.execute(sql_text(f"ALTER TABLE disease_cases ADD COLUMN {ddl}"))
        conn.execute(sql_text("""CREATE TABLE IF NOT EXISTS case_reviews (
            review_id INT AUTO_INCREMENT PRIMARY KEY, case_id INT NOT NULL, reviewer_id INT NULL,
            action VARCHAR(20) NOT NULL, status_after VARCHAR(20) NOT NULL,
            lab_test VARCHAR(100) NULL,
            lab_result ENUM('Not done','Pending','Positive','Negative') NOT NULL DEFAULT 'Not done',
            notes TEXT NULL, created_at DATETIME DEFAULT CURRENT_TIMESTAMP, INDEX idx_case (case_id))"""))


def _role_uid(u):
    g = (lambda k: u.get(k)) if isinstance(u, dict) else (lambda k: getattr(u, k, None))
    return g("role"), g("user_id")


@router.get("/cases/{case_id}")
async def case_detail(case_id: int, db: Session = Depends(get_db), current_user = Depends(get_current_user)):
    row = db.query(DiseaseCase, Disease).join(Disease, Disease.disease_id == DiseaseCase.disease_id
          ).filter(DiseaseCase.case_id == case_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Case not found.")
    case, dis = row
    pat = rec = None
    if case.patient_id:
        pat = db.query(Patient).filter(Patient.patient_id == case.patient_id).first()
        q = db.query(MedicalRecord).filter(MedicalRecord.patient_id == case.patient_id)
        rec = (q.filter(MedicalRecord.visit_date == case.date_recorded).order_by(MedicalRecord.record_id.desc()).first()
               or q.order_by(MedicalRecord.visit_date.desc()).first())
    revs = db.execute(sql_text(
        "SELECT r.action, r.status_after, r.lab_test, r.lab_result, r.notes, r.created_at, u.name AS by_name "
        "FROM case_reviews r LEFT JOIN users u ON u.user_id = r.reviewer_id "
        "WHERE r.case_id = :c ORDER BY r.review_id DESC"), {"c": case_id}).mappings().all()
    return {
        "case_id": case.case_id, "disease": dis.disease_name, "icd": dis.icd_code,
        "code": f"{''.join(ch for ch in dis.disease_name if ch.isalpha())[:3].upper()}-{case.case_id:03d}",
        "purok": extract_area(pat.address) if pat else "Other areas",
        "date": case.date_recorded.isoformat() if case.date_recorded else None,
        "status": case.case_status, "escalated": case.escalated_at is not None,
        "symptoms": rec.symptoms if rec else None, "diagnosis": rec.diagnosis if rec else None,
        "vitals": {"visit_date": rec.visit_date.isoformat() if rec and rec.visit_date else None,
                   "blood_pressure": rec.blood_pressure, "temperature": float(rec.temperature) if rec and rec.temperature is not None else None,
                   "weight_kg": float(rec.weight_kg) if rec and rec.weight_kg is not None else None,
                   "heart_rate": rec.heart_rate, "respiratory_rate": rec.respiratory_rate} if rec else {},
        "reviews": [{"by": r["by_name"], "action": r["action"], "status": r["status_after"], "lab_test": r["lab_test"],
                     "lab_result": r["lab_result"], "notes": r["notes"],
                     "at": r["created_at"].isoformat() if r["created_at"] else None} for r in revs],
    }


@router.post("/cases/{case_id}/review")
async def review_case(case_id: int, payload: dict = Body(...), db: Session = Depends(get_db),
                      current_user = Depends(get_current_user)):
    role, uid = _role_uid(current_user)
    action = (payload or {}).get("action")
    if action not in REVIEW_ACTIONS.get(role, ()):
        raise HTTPException(status_code=403, detail="Your role cannot perform this action.")
    case = db.query(DiseaseCase).filter(DiseaseCase.case_id == case_id).first()
    if not case:
        raise HTTPException(status_code=404, detail="Case not found.")
    if case.case_status not in HUB_PENDING:
        raise HTTPException(status_code=409, detail=f"Case is already {case.case_status}.")

    lab = payload.get("lab_result") or "Not done"
    if lab not in LAB_RESULTS:
        raise HTTPException(status_code=400, detail="Invalid lab result.")
    lab_test = (payload.get("lab_test") or "").strip()[:100] or None
    notes    = (payload.get("notes") or "").strip() or None
    prev = db.execute(sql_text("SELECT lab_result FROM case_reviews WHERE case_id=:c AND lab_result<>'Not done' "
                               "ORDER BY review_id DESC LIMIT 1"), {"c": case_id}).scalar()
    eff_lab = lab if lab != "Not done" else (prev or "Not done")

    status = case.case_status
    if action in ("draft", "escalate"):
        status = payload.get("status") if payload.get("status") in HUB_PENDING else "Probable"
    if action == "escalate" and eff_lab == "Not done" and not notes:
        raise HTTPException(status_code=400, detail="Add a lab result or notes before escalating to the doctor.")
    if action == "confirm":
        if eff_lab != "Positive" and not notes:
            raise HTTPException(status_code=400, detail="No positive lab result. Add the reason for confirming.")
        status = "Confirmed"
    if action == "reject":
        if not notes:
            raise HTTPException(status_code=400, detail="Add the reason for rejecting this case.")
        status = "Rejected"

    now = datetime.now()
    case.case_status = status
    if action == "escalate":
        case.escalated_by, case.escalated_at = uid, now
    if action in ("confirm", "reject"):
        case.verified_by, case.verified_at = uid, now
    db.execute(sql_text("INSERT INTO case_reviews (case_id, reviewer_id, action, status_after, lab_test, lab_result, notes) "
                        "VALUES (:c,:u,:a,:s,:t,:l,:n)"),
               {"c": case_id, "u": uid, "a": action, "s": status, "t": lab_test, "l": lab, "n": notes})
    db.commit()
    return {"case_id": case_id, "case_status": status, "escalated": case.escalated_at is not None}
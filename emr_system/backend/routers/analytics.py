# ============================================================
# routers/analytics.py — Disease Trend Analytics
# Single-barangay — no barangay FK or dropdown needed
# All data is from the current database instance
# ============================================================

from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func, extract
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
    total_patients = db.query(Patient).filter(Patient.is_archived == False).count()
    total_records  = db.query(MedicalRecord).count()
    total_immun    = db.query(Immunization).count()
    total_female   = db.query(Patient).filter(
        Patient.is_archived == False, Patient.sex == "Female"
    ).count()

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
    total_bhw  = db.query(User).filter(User.role == "bhw").count()
    active_bhw = db.query(User).filter(User.role == "bhw", User.status == "active").count()

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
    current_user = Depends(get_current_user)
):
    """Distribusyon ng edad ng mga pasyente para sa bar chart."""
    rows = db.query(Patient.birthdate, Patient.sex).filter(
        Patient.is_archived == False
    ).all()

    if not rows:
        return {"labels": [], "male": [], "female": []}

    df    = pd.DataFrame(rows, columns=['birthdate','sex'])
    today = pd.Timestamp.now()
    df['age'] = (today - pd.to_datetime(df['birthdate'])).dt.days // 365

    bins   = [0, 4, 12, 17, 35, 59, 150]
    labels = ['0-4','5-12','13-17','18-35','36-59','60+']
    df['age_group'] = pd.cut(df['age'], bins=bins, labels=labels, right=True)

    male   = df[df['sex']=='Male'].groupby('age_group', observed=True).size()
    female = df[df['sex']=='Female'].groupby('age_group', observed=True).size()

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
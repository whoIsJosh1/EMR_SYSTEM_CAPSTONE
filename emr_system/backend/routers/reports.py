# ============================================================
# routers/reports.py - PDF Report Generation Routes
# Gumagamit ng ReportLab para gumawa ng professional na PDF reports
# Admin only ang pwedeng mag-generate at mag-download ng reports
# ============================================================

from fastapi import APIRouter, Depends, HTTPException, status, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from sqlalchemy import func, extract
from typing import Optional
from datetime import date, datetime, timedelta
import io
import pandas as pd

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch, cm
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph,
    Spacer, HRFlowable, KeepTogether
)
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT

from database import get_db
from models.models import Patient, MedicalRecord, Immunization, DiseaseCase, Disease
from middleware.auth import require_admin, get_current_user

router = APIRouter(prefix="/api/reports", tags=["Reports"])

# ============================================================
# HELPER FUNCTIONS PARA SA PDF STYLING
# ============================================================

# Kulay na gagamitin sa PDF
PRIMARY_COLOR   = colors.HexColor('#0a4f76')
SECONDARY_COLOR = colors.HexColor('#1a8a5e')
ACCENT_COLOR    = colors.HexColor('#e8f4f8')
LIGHT_GREEN     = colors.HexColor('#e8f8f0')
TEXT_DARK       = colors.HexColor('#1a1a2e')
GRAY_LIGHT      = colors.HexColor('#f8f9fa')


def create_pdf_header(elements: list, title: str, subtitle: str, date_range: str = ""):
    """
    Gumawa ng consistent na header para sa lahat ng PDF reports.
    Kasama ang logo area, titulo, at petsa ng report.
    """
    styles = getSampleStyleSheet()

    # Title style
    title_style = ParagraphStyle(
        'ReportTitle',
        parent=styles['Title'],
        fontSize=18,
        fontName='Helvetica-Bold',
        textColor=PRIMARY_COLOR,
        spaceAfter=4,
        alignment=TA_CENTER
    )

    subtitle_style = ParagraphStyle(
        'ReportSubtitle',
        parent=styles['Normal'],
        fontSize=12,
        fontName='Helvetica',
        textColor=SECONDARY_COLOR,
        spaceAfter=4,
        alignment=TA_CENTER
    )

    info_style = ParagraphStyle(
        'ReportInfo',
        parent=styles['Normal'],
        fontSize=9,
        fontName='Helvetica',
        textColor=colors.gray,
        alignment=TA_CENTER
    )

    # Header content
    elements.append(Paragraph("🏥 District 1 Health Office", subtitle_style))
    elements.append(Paragraph(title, title_style))
    elements.append(Paragraph(subtitle, subtitle_style))

    if date_range:
        elements.append(Paragraph(date_range, info_style))

    elements.append(Paragraph(
        f"Generated on: {datetime.now().strftime('%B %d, %Y at %I:%M %p')}",
        info_style
    ))
    elements.append(HRFlowable(width="100%", thickness=2, color=PRIMARY_COLOR))
    elements.append(Spacer(1, 0.2 * inch))


def create_summary_table(data: list, style_color=PRIMARY_COLOR) -> Table:
    """
    Gumawa ng styled na table para sa mga report.
    Ang unang row ay treated bilang header.
    """
    if not data:
        return None

    table = Table(data, repeatRows=1)

    # Number of columns
    num_cols = len(data[0])
    header_bg = style_color
    alt_row_bg = ACCENT_COLOR

    style = TableStyle([
        # Header row styling
        ('BACKGROUND',    (0, 0), (-1, 0),  header_bg),
        ('TEXTCOLOR',     (0, 0), (-1, 0),  colors.white),
        ('FONTNAME',      (0, 0), (-1, 0),  'Helvetica-Bold'),
        ('FONTSIZE',      (0, 0), (-1, 0),  9),
        ('ALIGN',         (0, 0), (-1, 0),  'CENTER'),
        ('TOPPADDING',    (0, 0), (-1, 0),  8),
        ('BOTTOMPADDING', (0, 0), (-1, 0),  8),

        # Data rows styling
        ('FONTNAME',      (0, 1), (-1, -1), 'Helvetica'),
        ('FONTSIZE',      (0, 1), (-1, -1), 8),
        ('TOPPADDING',    (0, 1), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 1), (-1, -1), 5),
        ('ALIGN',         (0, 1), (-1, -1), 'LEFT'),

        # Alternating row colors para mas madaling basahin
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, alt_row_bg]),

        # Borders
        ('GRID',          (0, 0), (-1, -1), 0.5, colors.HexColor('#dee2e6')),
        ('LINEBELOW',     (0, 0), (-1, 0),  1, header_bg),
    ])

    table.setStyle(style)
    return table


# ============================================================
# PATIENT RECORDS REPORT
# ============================================================
@router.get("/patients")
async def generate_patient_report(
    db: Session = Depends(get_db),
    current_user = Depends(require_admin),  # Admin only
    date_from:    Optional[date] = Query(None),
    date_to:      Optional[date] = Query(None)
):
    """
    Gumawa ng PDF report ng patient records.
    Admin only. May options para sa filter ng barangay at petsa.
    """
    # Kuhanin ang patient data
    from database import get_barangay_name
    barangay_name = get_barangay_name()

    query = db.query(Patient).filter(Patient.is_archived == False)

    if date_from:
        query = query.filter(Patient.created_at >= date_from)
    if date_to:
        query = query.filter(Patient.created_at < date_to + timedelta(days=1))

    results = query.order_by(Patient.last_name).all()

    if not results:
        raise HTTPException(status_code=404, detail="No patient records found.")

    # Gumawa ng PDF sa memory (walang file na ginagawa sa disk)
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        rightMargin=1 * cm,
        leftMargin=1 * cm,
        topMargin=1.5 * cm,
        bottomMargin=1 * cm
    )

    elements = []
    styles = getSampleStyleSheet()

    # Header
    subtitle = barangay_name
    date_range = ""
    if date_from or date_to:
        date_range = f"Period: {date_from or 'Start'} to {date_to or 'Present'}"

    create_pdf_header(elements, "Patient Records Report", subtitle, date_range)

    # Summary statistics
    total = len(results)
    male_count   = sum(1 for r in results if r.sex == "Male")
    female_count = sum(1 for r in results if r.sex == "Female")

    summary_data = [
        ['Total Patients', 'Male', 'Female'],
        [str(total), str(male_count), str(female_count)]
    ]
    summary_table = Table(summary_data, colWidths=[2 * inch, 2 * inch, 2 * inch])
    summary_table.setStyle(TableStyle([
        ('BACKGROUND',    (0, 0), (-1, 0), PRIMARY_COLOR),
        ('TEXTCOLOR',     (0, 0), (-1, 0), colors.white),
        ('FONTNAME',      (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('BACKGROUND',    (0, 1), (-1, -1), LIGHT_GREEN),
        ('FONTNAME',      (0, 1), (-1, -1), 'Helvetica-Bold'),
        ('FONTSIZE',      (0, 1), (-1, -1), 14),
        ('ALIGN',         (0, 0), (-1, -1), 'CENTER'),
        ('GRID',          (0, 0), (-1, -1), 0.5, colors.gray),
        ('TOPPADDING',    (0, 0), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
    ]))

    elements.append(summary_table)
    elements.append(Spacer(1, 0.3 * inch))

    # Patient table
    headers = ['#', 'Last Name', 'First Name', 'Birthdate', 'Age', 'Sex', 'Contact #', 'PhilHealth', 'Address']
    table_data = [headers]

    for idx, patient in enumerate(results, 1):
        today = date.today()
        age   = today.year - patient.birthdate.year
        if (today.month, today.day) < (patient.birthdate.month, patient.birthdate.day):
            age -= 1

        table_data.append([
            str(idx),
            patient.last_name,
            patient.first_name,
            patient.birthdate.strftime("%m/%d/%Y"),
            str(age),
            patient.sex,
            patient.contact_number or "N/A",
            patient.philhealth_no  or "N/A",
            patient.address[:40] + "..." if len(patient.address) > 40 else patient.address
        ])

    col_widths = [0.4*inch, 1.3*inch, 1.3*inch, 1*inch, 0.5*inch, 0.6*inch, 1.2*inch, 1.1*inch, 3*inch]
    patient_table = create_summary_table(table_data)
    if patient_table:
        patient_table._argW = col_widths
        elements.append(patient_table)

    # I-build ang PDF
    doc.build(elements)
    buffer.seek(0)

    # I-log ang report generation")

    return StreamingResponse(
        buffer,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f"attachment; filename=patient_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
        }
    )


# ============================================================
# DISEASE TREND REPORT
# ============================================================
@router.get("/disease-trends")
async def generate_disease_report(
    db: Session = Depends(get_db),
    current_user = Depends(require_admin),
    year:        Optional[int]  = Query(None),
    barangay_id: Optional[int]  = Query(None)
):
    """
    Gumawa ng PDF report ng disease trends per barangay.
    Kasama ang monthly breakdown at per-barangay comparison.
    """
    target_year = year or datetime.now().year

    # Kuhanin ang disease case data
    query = db.query(
        Disease.disease_name,
        Barangay.barangay_name,
        DiseaseCase.date_recorded,
        func.sum(DiseaseCase.number_of_cases).label('total_cases')
    ).join(
        Disease,  DiseaseCase.disease_id == Disease.disease_id
    ).filter(
        extract('year', DiseaseCase.date_recorded) == target_year
    )

    if barangay_id:
        query = query.filter(DiseaseCase.barangay_id == barangay_id)

    results = query.group_by(
        Disease.disease_name, Barangay.barangay_name, DiseaseCase.date_recorded
    ).order_by(func.sum(DiseaseCase.number_of_cases).desc()).all()

    if not results:
        raise HTTPException(status_code=404, detail="No disease data found.")

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        rightMargin=1 * cm,
        leftMargin=1 * cm,
        topMargin=1.5 * cm,
        bottomMargin=1 * cm
    )

    elements = []

    from database import get_barangay_name
    create_pdf_header(
        elements,
        f"Disease Trend Report - {target_year}",
        get_barangay_name(),
        f"As of {datetime.now().strftime('%B %d, %Y')}"
    )

    # Gamitin ang Pandas para sa aggregation
    df = pd.DataFrame(results, columns=['disease_name', 'date_recorded', 'total_cases'])
    df['month'] = pd.to_datetime(df['date_recorded']).dt.strftime('%B')

    # Summary per disease
    disease_summary = df.groupby('disease_name')['total_cases'].sum().reset_index()
    disease_summary = disease_summary.sort_values('total_cases', ascending=False)

    styles = getSampleStyleSheet()
    section_style = ParagraphStyle(
        'SectionHeader',
        parent=styles['Heading2'],
        fontSize=12,
        fontName='Helvetica-Bold',
        textColor=SECONDARY_COLOR,
        spaceBefore=10,
        spaceAfter=5
    )

    elements.append(Paragraph("Summary by Disease", section_style))

    disease_headers = ['Disease Name', 'Total Cases', '% of Total']
    total_all = int(disease_summary['total_cases'].sum())
    disease_data = [disease_headers]

    for _, row in disease_summary.iterrows():
        percentage = (row['total_cases'] / total_all * 100) if total_all > 0 else 0
        disease_data.append([
            row['disease_name'],
            str(int(row['total_cases'])),
            f"{percentage:.1f}%"
        ])

    disease_data.append(['TOTAL', str(total_all), '100%'])  # Grand total row

    disease_table = Table(disease_data, colWidths=[4*inch, 2*inch, 2*inch])
    disease_table.setStyle(TableStyle([
        ('BACKGROUND',    (0, 0), (-1, 0),  PRIMARY_COLOR),
        ('TEXTCOLOR',     (0, 0), (-1, 0),  colors.white),
        ('FONTNAME',      (0, 0), (-1, 0),  'Helvetica-Bold'),
        ('FONTSIZE',      (0, 0), (-1, 0),  9),
        ('ALIGN',         (0, 0), (-1, -1), 'CENTER'),
        ('FONTNAME',      (0, 1), (-1, -2), 'Helvetica'),
        ('FONTSIZE',      (0, 1), (-1, -2), 9),
        ('ROWBACKGROUNDS',(0, 1), (-1, -2), [colors.white, ACCENT_COLOR]),
        ('BACKGROUND',    (0, -1),(-1, -1), SECONDARY_COLOR),
        ('TEXTCOLOR',     (0, -1),(-1, -1), colors.white),
        ('FONTNAME',      (0, -1),(-1, -1), 'Helvetica-Bold'),
        ('GRID',          (0, 0), (-1, -1), 0.5, colors.gray),
        ('TOPPADDING',    (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
    ]))

    elements.append(disease_table)

    # I-build ang PDF
    doc.build(elements)
    buffer.seek(0)

    return StreamingResponse(
        buffer,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f"attachment; filename=disease_trend_{target_year}_{datetime.now().strftime('%Y%m%d')}.pdf"
        }
    )


# ============================================================
# IMMUNIZATION REPORT
# ============================================================
@router.get("/immunization")
async def generate_immunization_report(
    db: Session = Depends(get_db),
    current_user = Depends(require_admin),
    date_from: Optional[date] = Query(None),
    date_to:   Optional[date] = Query(None)
):
    """
    Gumawa ng PDF report ng immunization records.
    V2: Walang barangay join — single-barangay database.
    """
    from database import get_barangay_name

    query = db.query(
        Immunization,
        Patient.first_name,
        Patient.last_name,
        Patient.sex
    ).join(
        Patient, Immunization.patient_id == Patient.patient_id
    )

    if date_from:
        query = query.filter(Immunization.date_given >= date_from)
    if date_to:
        query = query.filter(Immunization.date_given <= date_to)

    results = query.order_by(Immunization.date_given.desc()).all()

    if not results:
        raise HTTPException(status_code=404, detail="No immunization records found.")

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        rightMargin=1 * cm,
        leftMargin=1 * cm,
        topMargin=1.5 * cm,
        bottomMargin=1 * cm
    )

    elements = []
    barangay_name = get_barangay_name()

    create_pdf_header(elements, "Immunization Records Report", barangay_name)

    # Immunization table — walang Barangay column (single-barangay na)
    headers = ['#', 'Patient Name', 'Sex', 'Vaccine', 'Dose #', 'Date Given', 'Next Schedule', 'Administered By']
    table_data = [headers]

    for idx, row in enumerate(results, 1):
        immun = row.Immunization
        table_data.append([
            str(idx),
            f"{row.last_name}, {row.first_name}",
            row.sex,
            immun.vaccine_name,
            str(immun.dose_number or 1),
            immun.date_given.strftime("%m/%d/%Y"),
            immun.next_schedule.strftime("%m/%d/%Y") if immun.next_schedule else "N/A",
            immun.administered_by or "N/A"
        ])

    col_widths = [0.3*inch, 2.2*inch, 0.6*inch, 1.8*inch, 0.6*inch, 1*inch, 1.1*inch, 1.8*inch]
    immun_table = create_summary_table(table_data)
    if immun_table:
        immun_table._argW = col_widths
        elements.append(immun_table)

    doc.build(elements)
    buffer.seek(0)

    return StreamingResponse(
        buffer,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f"attachment; filename=immunization_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
        }
    )

# ============================================================
# AI DATA-DRIVEN INSIGHTS REPORT (placeholder)
# ============================================================
@router.get("/ai-insights")
async def generate_ai_insights_report(
    db: Session = Depends(get_db),
    current_user = Depends(require_admin),
    # Frontend sends ?date_from=...&date_to=... (same names as the other reports)
    start_date: Optional[date] = Query(None, alias="date_from"),
    end_date:   Optional[date] = Query(None, alias="date_to"),
):
    """Placeholder PDF. Palitan ang 'Insights' section ng totoong Gemini output sa susunod."""
    from database import get_barangay_name

    if start_date and end_date and start_date > end_date:
        raise HTTPException(status_code=400, detail="Start date must not be after end date.")

    date_range = ""
    if start_date or end_date:
        date_range = f"Period: {start_date or 'Start'} to {end_date or 'Present'}"

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        rightMargin=1.5 * cm, leftMargin=1.5 * cm,
        topMargin=1.5 * cm, bottomMargin=1.5 * cm
    )
    styles = getSampleStyleSheet()
    elements = []

    create_pdf_header(elements, "AI Data-Driven Insights Report", get_barangay_name(), date_range)

    elements.append(Paragraph("Insights", styles['Heading2']))
    elements.append(Paragraph(
        "Placeholder: AI-generated insights for the selected period will appear here.",
        styles['BodyText']
    ))
    elements.append(Spacer(1, 0.3 * inch))
    elements.append(Paragraph(
        "<i>The AI-generated results are decision-support information only and should not "
        "replace the professional judgment of qualified healthcare personnel.</i>",
        styles['BodyText']
    ))

    doc.build(elements)
    buffer.seek(0)

    return StreamingResponse(
        buffer,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f"attachment; filename=ai_insights_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
        }
    )

# ============================================================
# CENTRAL DATE-FILTER REPORTS
# Disease Timeline, Disease Surveillance, Audit Trail, Inventory
# Lahat ay tumatanggap ng ?date_from=YYYY-MM-DD&date_to=YYYY-MM-DD
# ============================================================
from collections import Counter, defaultdict
from datetime import timedelta
from models.models import User, AuditLog, InventoryItem, InventoryTransaction

EXPIRY_WARNING_DAYS = 90      # "Expiring soon" threshold sa Inventory report
MAX_LOG_ROWS        = 5000    # limit para hindi sobrang laki ng PDF


def _check_range(date_from, date_to):
    if date_from and date_to and date_from > date_to:
        raise HTTPException(status_code=400, detail="Start date must not be after end date.")


def _period_text(date_from, date_to) -> str:
    if date_from or date_to:
        return f"Period: {date_from or 'Start'} to {date_to or 'Present'}"
    return "Period: All dates"


def _new_doc(buffer, portrait: bool = False) -> SimpleDocTemplate:
    return SimpleDocTemplate(
        buffer,
        pagesize=A4 if portrait else landscape(A4),
        rightMargin=1 * cm, leftMargin=1 * cm,
        topMargin=1.5 * cm, bottomMargin=1 * cm
    )


def _section_title(text: str) -> Paragraph:
    styles = getSampleStyleSheet()
    return Paragraph(text, ParagraphStyle(
        'SectionHeader', parent=styles['Heading2'], fontSize=12,
        fontName='Helvetica-Bold', textColor=SECONDARY_COLOR,
        spaceBefore=10, spaceAfter=5
    ))


def _note(text: str) -> Paragraph:
    styles = getSampleStyleSheet()
    return Paragraph(text, ParagraphStyle(
        'ReportNote', parent=styles['Normal'], fontSize=8,
        textColor=colors.gray, spaceAfter=6
    ))


def _clip(text, limit: int) -> str:
    text = str(text or "")
    return text if len(text) <= limit else text[:limit - 3] + "..."


def _age_on(birthdate, on_date) -> str:
    if not birthdate or not on_date:
        return "N/A"
    return str(on_date.year - birthdate.year
               - ((on_date.month, on_date.day) < (birthdate.month, birthdate.day)))


def _stat_row(pairs: list) -> Table:
    """Maliit na summary table. pairs = [('Total', 12), ('Male', 5)]"""
    table = Table(
        [[label for label, _ in pairs], [str(value) for _, value in pairs]],
        colWidths=[1.7 * inch] * len(pairs)
    )
    table.setStyle(TableStyle([
        ('BACKGROUND',    (0, 0), (-1, 0), PRIMARY_COLOR),
        ('TEXTCOLOR',     (0, 0), (-1, 0), colors.white),
        ('FONTNAME',      (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE',      (0, 0), (-1, 0), 9),
        ('BACKGROUND',    (0, 1), (-1, -1), LIGHT_GREEN),
        ('FONTNAME',      (0, 1), (-1, -1), 'Helvetica-Bold'),
        ('FONTSIZE',      (0, 1), (-1, -1), 14),
        ('ALIGN',         (0, 0), (-1, -1), 'CENTER'),
        ('GRID',          (0, 0), (-1, -1), 0.5, colors.gray),
        ('TOPPADDING',    (0, 0), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
    ]))
    return table


def _table(data: list, col_widths: list = None) -> Table:
    """create_summary_table() + fixed column widths (same approach as the other reports)."""
    table = create_summary_table(data)
    if col_widths:
        table._argW = col_widths
    return table


def _finish_pdf(doc, buffer, elements: list, prefix: str) -> StreamingResponse:
    doc.build(elements)
    buffer.seek(0)
    return StreamingResponse(
        buffer,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f"attachment; filename={prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
        }
    )


# ============================================================
# DISEASE TIMELINE REPORT   (GET /api/reports/disease-timeline)
# Pumalit sa lumang /disease-trends (year-based, may Barangay na wala na)
# ============================================================
@router.get("/disease-timeline")
async def generate_disease_timeline_report(
    db: Session = Depends(get_db),
    current_user = Depends(require_admin),
    date_from: Optional[date] = Query(None),
    date_to:   Optional[date] = Query(None)
):
    """Disease cases sa napiling petsa. Hindi kasama ang 'Rejected' (kapareho ng analytics)."""
    from database import get_barangay_name
    _check_range(date_from, date_to)

    query = (
        db.query(DiseaseCase, Disease)
          .join(Disease, DiseaseCase.disease_id == Disease.disease_id)
          .filter(DiseaseCase.case_status != "Rejected")
    )
    if date_from:
        query = query.filter(DiseaseCase.date_recorded >= date_from)
    if date_to:
        query = query.filter(DiseaseCase.date_recorded <= date_to)

    results = query.order_by(DiseaseCase.date_recorded).all()
    if not results:
        raise HTTPException(status_code=404, detail="No disease cases found for the selected period.")

    by_disease = Counter()                 # disease -> total cases
    by_month   = defaultdict(Counter)      # "2026-10" -> {disease: cases}
    for case, disease in results:
        n = case.number_of_cases or 0
        by_disease[disease.disease_name] += n
        by_month[case.date_recorded.strftime("%Y-%m")][disease.disease_name] += n
    total_cases = sum(by_disease.values())

    buffer = io.BytesIO()
    doc = _new_doc(buffer)
    elements = []
    create_pdf_header(elements, "Disease Timeline Report", get_barangay_name(),
                      _period_text(date_from, date_to))

    elements.append(_stat_row([
        ("Total Cases", total_cases),
        ("Diseases Recorded", len(by_disease)),
        ("Case Records", len(results)),
    ]))

    # Cases by disease
    elements.append(_section_title("Cases by Disease"))
    rows = [['Disease', 'Cases', '% of Total']]
    for name, n in by_disease.most_common():
        pct = (n / total_cases * 100) if total_cases else 0
        rows.append([name, str(n), f"{pct:.1f}%"])
    rows.append(['TOTAL', str(total_cases), '100%'])
    disease_table = _table(rows, [4 * inch, 1.5 * inch, 1.5 * inch])
    disease_table.setStyle(TableStyle([
        ('FONTNAME',   (0, -1), (-1, -1), 'Helvetica-Bold'),
        ('BACKGROUND', (0, -1), (-1, -1), LIGHT_GREEN),
        ('ALIGN',      (1, 0),  (-1, -1), 'CENTER'),
    ]))
    elements.append(disease_table)

    # Monthly timeline
    elements.append(_section_title("Monthly Timeline"))
    rows = [['Month', 'Total Cases', 'Top Disease']]
    for key in sorted(by_month):
        counts = by_month[key]
        top_name, top_n = counts.most_common(1)[0]
        rows.append([
            datetime.strptime(key, "%Y-%m").strftime("%B %Y"),
            str(sum(counts.values())),
            f"{top_name} ({top_n})"
        ])
    month_table = _table(rows, [2 * inch, 1.5 * inch, 4 * inch])
    month_table.setStyle(TableStyle([('ALIGN', (1, 0), (1, -1), 'CENTER')]))
    elements.append(month_table)

    return _finish_pdf(doc, buffer, elements, "disease_timeline")


# ============================================================
# DISEASE SURVEILLANCE REPORT   (GET /api/reports/surveillance)
# Probable + Confirmed cases lang (para sa DOH PIDSR)
# ============================================================
@router.get("/surveillance")
async def generate_surveillance_report(
    db: Session = Depends(get_db),
    current_user = Depends(require_admin),
    date_from: Optional[date] = Query(None),
    date_to:   Optional[date] = Query(None)
):
    from database import get_barangay_name
    _check_range(date_from, date_to)

    query = (
        db.query(DiseaseCase, Disease, Patient, User)
          .join(Disease, DiseaseCase.disease_id == Disease.disease_id)
          .outerjoin(Patient, DiseaseCase.patient_id == Patient.patient_id)
          .outerjoin(User, DiseaseCase.verified_by == User.user_id)
          .filter(DiseaseCase.case_status.in_(["Probable", "Confirmed"]))
    )
    if date_from:
        query = query.filter(DiseaseCase.date_recorded >= date_from)
    if date_to:
        query = query.filter(DiseaseCase.date_recorded <= date_to)

    results = query.order_by(DiseaseCase.date_recorded.desc(), Disease.disease_name).all()
    if not results:
        raise HTTPException(status_code=404, detail="No Probable or Confirmed cases found for the selected period.")

    probable  = sum((c.number_of_cases or 0) for c, *_ in results if c.case_status == "Probable")
    confirmed = sum((c.number_of_cases or 0) for c, *_ in results if c.case_status == "Confirmed")

    buffer = io.BytesIO()
    doc = _new_doc(buffer)
    elements = []
    create_pdf_header(elements, "Disease Surveillance Report", get_barangay_name(),
                      _period_text(date_from, date_to))
    elements.append(_note("Compiled logs of Probable and Confirmed cases for local DOH PIDSR reporting. "
                          "Suspected and Rejected cases are not included."))
    elements.append(_stat_row([
        ("Probable Cases", probable),
        ("Confirmed Cases", confirmed),
        ("Total Cases", probable + confirmed),
    ]))

    elements.append(_section_title("Case Log"))
    rows = [['#', 'Date', 'Disease', 'ICD', 'Patient', 'Age', 'Sex', 'Address', 'Cases', 'Status', 'Verified By']]
    for idx, (case, disease, patient, verifier) in enumerate(results, 1):
        rows.append([
            str(idx),
            case.date_recorded.strftime("%m/%d/%Y"),
            _clip(disease.disease_name, 28),
            disease.icd_code or "N/A",
            f"{patient.last_name}, {patient.first_name}" if patient else "N/A",
            _age_on(patient.birthdate, case.date_recorded) if patient else "N/A",
            patient.sex if patient else "N/A",
            _clip(patient.address, 34) if patient else "N/A",
            str(case.number_of_cases or 0),
            case.case_status,
            verifier.name if verifier else "N/A",
        ])
    widths = [0.3*inch, 0.8*inch, 1.7*inch, 0.6*inch, 1.5*inch, 0.4*inch,
              0.55*inch, 2.1*inch, 0.5*inch, 0.8*inch, 1.2*inch]
    elements.append(_table(rows, widths))

    return _finish_pdf(doc, buffer, elements, "disease_surveillance")


# ============================================================
# SYSTEM AUDIT TRAIL REPORT   (GET /api/reports/audit-trail)
# Kung ano ang naka-save sa audit_log table sa napiling petsa
# ============================================================
@router.get("/audit-trail")
async def generate_audit_trail_report(
    db: Session = Depends(get_db),
    current_user = Depends(require_admin),
    date_from: Optional[date] = Query(None),
    date_to:   Optional[date] = Query(None)
):
    from database import get_barangay_name
    _check_range(date_from, date_to)

    query = db.query(AuditLog, User).outerjoin(User, AuditLog.user_id == User.user_id)
    if date_from:
        query = query.filter(AuditLog.date_time >= date_from)
    if date_to:
        # DateTime column: isama ang buong huling araw
        query = query.filter(AuditLog.date_time < date_to + timedelta(days=1))

    results = query.order_by(AuditLog.date_time.desc()).limit(MAX_LOG_ROWS + 1).all()
    if not results:
        raise HTTPException(status_code=404, detail="No audit log entries found for the selected period.")

    truncated = len(results) > MAX_LOG_ROWS
    results = results[:MAX_LOG_ROWS]
    action_counts = Counter(log.action for log, _ in results)

    buffer = io.BytesIO()
    doc = _new_doc(buffer)
    elements = []
    create_pdf_header(elements, "System Audit Trail Report", get_barangay_name(),
                      _period_text(date_from, date_to))
    if truncated:
        elements.append(_note(f"Showing the latest {MAX_LOG_ROWS:,} entries only. "
                              "Narrow the date range to see older entries."))

    elements.append(_stat_row([
        ("Entries Listed", len(results)),
        ("Distinct Actions", len(action_counts)),
        ("Users Involved", len({u.user_id for _, u in results if u})),
    ]))

    elements.append(_section_title("Activity Summary"))
    summary_rows = [['Action', 'Count']] + [[a, str(n)] for a, n in action_counts.most_common()]
    elements.append(_table(summary_rows, [4 * inch, 1.2 * inch]))

    elements.append(_section_title("Activity Log"))
    rows = [['#', 'Date & Time', 'User', 'Role', 'Action', 'IP Address']]
    for idx, (log, user) in enumerate(results, 1):
        rows.append([
            str(idx),
            log.date_time.strftime("%m/%d/%Y %I:%M %p") if log.date_time else "N/A",
            _clip(user.name if user else "System", 30),
            (user.role or "").upper() if user else "N/A",
            _clip(log.action, 50),
            log.ip_address or "N/A",
        ])
    elements.append(_table(rows, [0.4*inch, 1.5*inch, 2.2*inch, 0.9*inch, 3.4*inch, 1.4*inch]))

    return _finish_pdf(doc, buffer, elements, "audit_trail")


# ============================================================
# INVENTORY REPORT   (GET /api/reports/inventory)
# Stock levels = kasalukuyan. Ang date range ay para sa movement log.
# ============================================================
@router.get("/inventory")
async def generate_inventory_report(
    db: Session = Depends(get_db),
    current_user = Depends(require_admin),
    date_from: Optional[date] = Query(None),
    date_to:   Optional[date] = Query(None)
):
    from database import get_barangay_name
    _check_range(date_from, date_to)

    today = date.today()
    soon  = today + timedelta(days=EXPIRY_WARNING_DAYS)

    items = (
        db.query(InventoryItem)
          .filter(InventoryItem.is_active == True)
          .order_by(InventoryItem.category, InventoryItem.item_name)
          .all()
    )
    if not items:
        raise HTTPException(status_code=404, detail="No inventory items found.")

    def stock_status(it) -> str:
        stock = it.current_stock or 0
        if it.expiration_date and it.expiration_date < today:
            return "Expired"
        if stock <= 0:
            return "Out of stock"
        if stock <= (it.reorder_level or 0):
            return "Low stock"
        if it.expiration_date and it.expiration_date <= soon:
            return "Expiring soon"
        return "OK"

    statuses = [stock_status(it) for it in items]
    counts   = Counter(statuses)

    buffer = io.BytesIO()
    doc = _new_doc(buffer)
    elements = []
    create_pdf_header(elements, "Inventory Report", get_barangay_name(),
                      f"Stock as of {today.strftime('%B %d, %Y')}  |  Movements: {_period_text(date_from, date_to)[8:]}")
    elements.append(_note(f"Stock levels show the current inventory. The selected date range applies to the "
                          f"Stock Movements section only. 'Expiring soon' = within {EXPIRY_WARNING_DAYS} days."))
    elements.append(_stat_row([
        ("Total Items", len(items)),
        ("Out of Stock", counts["Out of stock"]),
        ("Low Stock", counts["Low stock"]),
        ("Expiring Soon", counts["Expiring soon"]),
        ("Expired", counts["Expired"]),
    ]))

    # Current stock
    elements.append(_section_title("Current Stock"))
    rows = [['#', 'Code', 'Item Name', 'Category', 'Unit', 'Stock', 'Reorder Lvl', 'Batch No.', 'Expiry', 'Status']]
    for idx, (it, status) in enumerate(zip(items, statuses), 1):
        rows.append([
            str(idx), it.item_code, _clip(it.item_name, 40), it.category, it.unit,
            str(it.current_stock or 0), str(it.reorder_level or 0),
            it.batch_number or "N/A",
            it.expiration_date.strftime("%m/%d/%Y") if it.expiration_date else "N/A",
            status
        ])
    stock_table = _table(rows, [0.3*inch, 0.9*inch, 2.4*inch, 1.1*inch, 0.7*inch,
                                0.6*inch, 0.8*inch, 1.1*inch, 0.9*inch, 1.0*inch])
    highlight = []
    for row_no, status in enumerate(statuses, 1):          # row 0 = header
        if status in ("Expired", "Out of stock"):
            highlight.append(('TEXTCOLOR', (9, row_no), (9, row_no), colors.HexColor('#c0392b')))
            highlight.append(('FONTNAME',  (9, row_no), (9, row_no), 'Helvetica-Bold'))
        elif status in ("Low stock", "Expiring soon"):
            highlight.append(('TEXTCOLOR', (9, row_no), (9, row_no), colors.HexColor('#d68910')))
            highlight.append(('FONTNAME',  (9, row_no), (9, row_no), 'Helvetica-Bold'))
    if highlight:
        stock_table.setStyle(TableStyle(highlight))
    elements.append(stock_table)

    # Stock movements (filtered by date range)
    tx_query = (
        db.query(InventoryTransaction, InventoryItem, User)
          .join(InventoryItem, InventoryTransaction.item_id == InventoryItem.item_id)
          .outerjoin(User, InventoryTransaction.user_id == User.user_id)
    )
    if date_from:
        tx_query = tx_query.filter(InventoryTransaction.created_at >= date_from)
    if date_to:
        tx_query = tx_query.filter(InventoryTransaction.created_at < date_to + timedelta(days=1))
    movements = tx_query.order_by(InventoryTransaction.created_at.desc()).limit(MAX_LOG_ROWS).all()

    elements.append(_section_title("Stock Movements"))
    if not movements:
        elements.append(_note("No stock movements recorded in the selected period."))
    else:
        received  = sum(abs(t.quantity) for t, _, _ in movements if t.transaction_type in ("import", "stock_in"))
        dispensed = sum(abs(t.quantity) for t, _, _ in movements if t.transaction_type == "dispense")
        adjusted  = sum(1 for t, _, _ in movements if t.transaction_type == "adjustment")
        elements.append(_stat_row([
            ("Units Received", received),
            ("Units Dispensed", dispensed),
            ("Adjustments", adjusted),
        ]))
        elements.append(Spacer(1, 0.15 * inch))

        rows = [['#', 'Date & Time', 'Item', 'Type', 'Qty', 'Before', 'After', 'By', 'Remarks']]
        for idx, (tx, it, user) in enumerate(movements, 1):
            rows.append([
                str(idx),
                tx.created_at.strftime("%m/%d/%Y %I:%M %p") if tx.created_at else "N/A",
                _clip(it.item_name, 32),
                str(tx.transaction_type).replace("_", " ").title(),
                str(tx.quantity), str(tx.stock_before), str(tx.stock_after),
                _clip(user.name if user else "System", 22),
                _clip(tx.remarks, 38),
            ])
        elements.append(_table(rows, [0.3*inch, 1.4*inch, 2.2*inch, 0.9*inch, 0.5*inch,
                                      0.6*inch, 0.6*inch, 1.5*inch, 2.5*inch]))

    return _finish_pdf(doc, buffer, elements, "inventory_report")
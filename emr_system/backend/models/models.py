# ============================================================
# models/models.py — SQLAlchemy ORM Models v2
# No barangay FK — each database IS one barangay
# Includes: Patient, HealthProblems, Pregnancy, MedicalRecord,
#           Immunization, Disease, DiseaseCases, AuditLog
# ============================================================

from sqlalchemy import (
    Column, Integer, String, Text, Date, DateTime,
    Enum, ForeignKey, DECIMAL, Boolean, SmallInteger
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from database import Base


# ============================================================
# USER
# ============================================================
class User(Base):
    __tablename__ = "users"

    user_id         = Column(Integer, primary_key=True, autoincrement=True)
    name            = Column(String(150), nullable=False)
    email           = Column(String(150), nullable=False, unique=True)
    password_hash   = Column(String(255), nullable=False)
    role            = Column(Enum("admin", "bhw", "midwife", "doctor","nurse"), nullable=False, default="bhw")
    position        = Column(String(100), nullable=True)
    status          = Column(Enum("active", "inactive", "locked"), nullable=False, default="active")
    is_first_login  = Column(Boolean, nullable=False, default=True)   # True = must change password on first login
    failed_attempts = Column(Integer, nullable=False, default=0)
    locked_until    = Column(DateTime, nullable=True)
    last_login      = Column(DateTime, nullable=True)
    created_at      = Column(DateTime, server_default=func.now())
    updated_at      = Column(DateTime, server_default=func.now(), onupdate=func.now())

    audit_logs      = relationship("AuditLog",      back_populates="user")
    medical_records = relationship("MedicalRecord", back_populates="encoder")
    immunizations   = relationship("Immunization",  back_populates="encoder")
    disease_cases   = relationship("DiseaseCase",   back_populates="encoder")
    pregnancies     = relationship("Pregnancy",     back_populates="encoder")


# ============================================================
# PATIENT
# ============================================================
class Patient(Base):
    __tablename__ = "patient"

    patient_id       = Column(Integer, primary_key=True, index=True, autoincrement=True)
    last_name        = Column(String(100), nullable=False)
    first_name       = Column(String(100), nullable=False)
    middle_name      = Column(String(100), nullable=True)
    birthdate        = Column(Date, nullable=False)
    sex              = Column(Enum("Male", "Female"), nullable=False)
    civil_status     = Column(Enum("Single", "Married", "Widowed", "Separated"), nullable=True)
    address          = Column(Text, nullable=False)
    contact_number   = Column(String(20), nullable=True)
    philhealth_no    = Column(String(30), nullable=True)
    occupation       = Column(String(150), nullable=True)
    mother_name      = Column(String(150), nullable=True)
    father_name      = Column(String(150), nullable=True)
    guardian_contact = Column(String(20), nullable=True)
    is_archived      = Column(Boolean, nullable=False, default=False)
    created_at       = Column(DateTime, server_default=func.now())
    updated_at       = Column(DateTime, server_default=func.now(), onupdate=func.now())

    medical_records  = relationship("MedicalRecord",  back_populates="patient", cascade="all, delete-orphan")
    immunizations    = relationship("Immunization",    back_populates="patient", cascade="all, delete-orphan")
    disease_cases    = relationship("DiseaseCase",     back_populates="patient")
    health_problems  = relationship("HealthProblem",   back_populates="patient", uselist=False, cascade="all, delete-orphan")
    pregnancies      = relationship("Pregnancy",       back_populates="patient", cascade="all, delete-orphan")


# ============================================================
# HEALTH PROBLEMS (input-only, NOT used in case counting)
# ============================================================
class HealthProblem(Base):
    __tablename__ = "health_problems"

    problem_id       = Column(Integer, primary_key=True, autoincrement=True)
    patient_id       = Column(Integer, ForeignKey("patient.patient_id", ondelete="CASCADE"),
                               nullable=False, unique=True)
    allergies        = Column(Text, nullable=True)
    has_asthma       = Column(Boolean, nullable=False, default=False)
    chronic_diseases = Column(Text, nullable=True)
    other_concerns   = Column(Text, nullable=True)
    created_at       = Column(DateTime, server_default=func.now())
    updated_at       = Column(DateTime, server_default=func.now(), onupdate=func.now())

    patient = relationship("Patient", back_populates="health_problems")


# ============================================================
# PREGNANCY (Female patients only)
# ============================================================
class Pregnancy(Base):
    __tablename__ = "pregnancy"

    pregnancy_id        = Column(Integer, primary_key=True, autoincrement=True)
    patient_id          = Column(Integer, ForeignKey("patient.patient_id", ondelete="CASCADE"),
                                  nullable=False)
    status              = Column(Enum("Pregnant", "Delivered", "Miscarriage", "Unknown"),
                                  nullable=False, default="Pregnant")
    gravida             = Column(Integer, nullable=True)
    para                = Column(Integer, nullable=True)
    lmp                 = Column(Date, nullable=True)
    expected_due_date   = Column(Date, nullable=True)
    delivery_date       = Column(Date, nullable=True)
    delivery_type       = Column(Enum("Normal", "CS", "Assisted"), nullable=True)
    birth_outcome       = Column(String(200), nullable=True)
    prenatal_visits     = Column(Integer, nullable=False, default=0)
    last_prenatal_date  = Column(Date, nullable=True)
    attending_physician = Column(String(150), nullable=True)
    remarks             = Column(Text, nullable=True)
    user_id             = Column(Integer, ForeignKey("users.user_id"), nullable=False)
    created_at          = Column(DateTime, server_default=func.now())
    updated_at          = Column(DateTime, server_default=func.now(), onupdate=func.now())

    patient = relationship("Patient",  back_populates="pregnancies")
    encoder = relationship("User",     back_populates="pregnancies")


# ============================================================
# MEDICAL RECORD
# ============================================================
class MedicalRecord(Base):
    __tablename__ = "medical_records"

    record_id        = Column(Integer, primary_key=True, index=True, autoincrement=True)
    patient_id       = Column(Integer, ForeignKey("patient.patient_id", ondelete="CASCADE"),
                               nullable=False)
    visit_date       = Column(Date,     nullable=False)
    chief_complaint  = Column(String(500), nullable=True)
    symptoms         = Column(Text,    nullable=True)
    diagnosis        = Column(Text,    nullable=True)
    treatment        = Column(Text,    nullable=True)
    blood_pressure   = Column(String(20), nullable=True)
    temperature      = Column(DECIMAL(4, 1), nullable=True)
    weight_kg        = Column(DECIMAL(5, 2), nullable=True)
    height_cm        = Column(DECIMAL(5, 2), nullable=True)
    heart_rate       = Column(Integer, nullable=True)
    respiratory_rate = Column(Integer, nullable=True)
    lmp              = Column(Date,    nullable=True)  # Female only
    notes            = Column(Text,    nullable=True)
    user_id          = Column(Integer, ForeignKey("users.user_id"), nullable=False)
    created_at       = Column(DateTime, server_default=func.now())
    updated_at       = Column(DateTime, server_default=func.now(), onupdate=func.now())

    patient = relationship(
    "Patient",
    back_populates="medical_records"
)

    encoder = relationship(
        "User",
        back_populates="medical_records"
    )

    prescriptions = relationship(
        "MedicalRecordPrescription",
        back_populates="medical_record",
        cascade="all, delete-orphan"
    )

# ============================================================
# MEDICAL RECORD PRESCRIPTION
# ============================================================
class MedicalRecordPrescription(Base):
    __tablename__ = "medical_record_prescriptions"

    prescription_id = Column(
        Integer,
        primary_key=True,
        index=True,
        autoincrement=True
    )

    record_id = Column(
        Integer,
        ForeignKey(
            "medical_records.record_id",
            ondelete="CASCADE"
        ),
        nullable=False,
        index=True
    )

    item_id = Column(
        Integer,
        ForeignKey("inventory_items.item_id"),
        nullable=False,
        index=True
    )

    quantity = Column(
        Integer,
        nullable=False
    )

    instructions = Column(
        Text,
        nullable=True
    )

    status = Column(
        Enum(
            "pending",
            "dispensed",
            "cancelled"
        ),
        nullable=False,
        default="pending"
    )

    dispensed_quantity = Column(
        Integer,
        nullable=False,
        default=0
    )

    dispensed_by = Column(
        Integer,
        ForeignKey("users.user_id"),
        nullable=True,
        index=True
    )

    dispensed_at = Column(
        DateTime,
        nullable=True
    )

    created_at = Column(
        DateTime,
        server_default=func.now()
    )

    updated_at = Column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now()
    )

    medical_record = relationship(
        "MedicalRecord",
        back_populates="prescriptions"
    )

    item = relationship(
        "InventoryItem"
    )

    dispenser = relationship(
        "User",
        foreign_keys=[dispensed_by]
    )
# ============================================================
# IMMUNIZATION
# ============================================================
class Immunization(Base):
    __tablename__ = "immunization"

    immunization_id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    patient_id      = Column(Integer, ForeignKey("patient.patient_id", ondelete="CASCADE"),
                              nullable=False)
    vaccine_name    = Column(String(150), nullable=False)
    date_given      = Column(Date,   nullable=False)
    dose_number     = Column(Integer, nullable=True, default=1)
    administered_by = Column(String(150), nullable=True)
    batch_number    = Column(String(50),  nullable=True)
    next_schedule   = Column(Date,   nullable=True)
    remarks         = Column(Text,   nullable=True)
    user_id         = Column(Integer, ForeignKey("users.user_id"), nullable=False)
    created_at      = Column(DateTime, server_default=func.now())

    patient = relationship("Patient", back_populates="immunizations")
    encoder = relationship("User",    back_populates="immunizations")


# ============================================================
# DISEASE (master list)
# ============================================================
class Disease(Base):
    __tablename__ = "disease"

    disease_id    = Column(Integer, primary_key=True, index=True, autoincrement=True)
    disease_name  = Column(String(200), nullable=False, unique=True)
    icd_code      = Column(String(20),  nullable=True)
    category      = Column(String(100), nullable=True)
    is_notifiable = Column(Boolean, nullable=False, default=False)
    created_at    = Column(DateTime, server_default=func.now())

    cases = relationship("DiseaseCase", back_populates="disease")


# ============================================================
# DISEASE CASES (auto-counted from diagnosis)
# ============================================================
class DiseaseCase(Base):
    __tablename__ = "disease_cases"

    case_id         = Column(Integer, primary_key=True, index=True, autoincrement=True)
    disease_id      = Column(Integer, ForeignKey("disease.disease_id"), nullable=False)
    patient_id      = Column(Integer, ForeignKey("patient.patient_id", ondelete="SET NULL"),
                              nullable=True)
    date_recorded   = Column(Date,    nullable=False)
    number_of_cases = Column(Integer, nullable=False, default=1)
    remarks         = Column(Text,    nullable=True)
    user_id         = Column(Integer, ForeignKey("users.user_id"), nullable=False)
    created_at      = Column(DateTime, server_default=func.now())

    disease = relationship("Disease",   back_populates="cases")
    patient = relationship("Patient",   back_populates="disease_cases")
    encoder = relationship("User",      back_populates="disease_cases")


# ============================================================
# AUDIT LOG (Login / Logout only)
# ============================================================
class AuditLog(Base):
    __tablename__ = "audit_log"

    log_id     = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_id    = Column(Integer, ForeignKey("users.user_id", ondelete="SET NULL"), nullable=True)
    action     = Column(String(100), nullable=False)
    ip_address = Column(String(45),  nullable=True)
    user_agent = Column(String(500), nullable=True)
    date_time  = Column(DateTime, server_default=func.now())

    user = relationship("User", back_populates="audit_logs")
# ============================================================
# INVENTORY ITEM
# ============================================================

class InventoryItem(Base):
    __tablename__ = "inventory_items"

    item_id = Column(Integer, primary_key=True, autoincrement=True)
    item_code = Column(String(50), unique=True, nullable=False, index=True)
    item_name = Column(String(150), nullable=False)
    category = Column(
        Enum("Medicine", "Vaccine", "Medical Supply"),
        nullable=False
    )
    unit = Column(String(30), nullable=False)
    current_stock = Column(Integer, nullable=False, default=0)
    reorder_level = Column(Integer, nullable=False, default=10)
    batch_number = Column(String(100), nullable=True)
    expiration_date = Column(Date, nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


# ============================================================
# INVENTORY TRANSACTION
# ============================================================

class InventoryTransaction(Base):
    __tablename__ = "inventory_transactions"

    transaction_id = Column(Integer, primary_key=True, autoincrement=True)
    item_id = Column(
        Integer,
        ForeignKey("inventory_items.item_id"),
        nullable=False
    )
    transaction_type = Column(
        Enum("import", "stock_in", "dispense", "adjustment"),
        nullable=False
    )
    quantity = Column(Integer, nullable=False)
    stock_before = Column(Integer, nullable=False)
    stock_after = Column(Integer, nullable=False)
    remarks = Column(Text, nullable=True)
    user_id = Column(Integer, ForeignKey("users.user_id"), nullable=True)
    created_at = Column(DateTime, server_default=func.now())

    item = relationship("InventoryItem")
    user = relationship("User")
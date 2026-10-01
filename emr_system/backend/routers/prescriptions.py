from datetime import datetime
from websocket_manager import manager
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from database import get_db
from models.models import (
    User,
    Patient,
    MedicalRecord,
    MedicalRecordPrescription,
    InventoryItem,
    InventoryTransaction,
)
from middleware.auth import get_current_user


router = APIRouter(
    prefix="/api/prescriptions",
    tags=["Prescriptions"]
)
# ============================================================
# Helpers
# ============================================================

def require_doctor(current_user: User):
    if current_user.role != "doctor":
        raise HTTPException(
            status_code=403,
            detail="Only doctors can create prescriptions."
        )
    return current_user


def require_nurse(current_user: User):
    if current_user.role != "nurse":
        raise HTTPException(
            status_code=403,
            detail="Only nurses can dispense prescriptions."
        )
    return current_user


def prescription_to_dict(prescription):
    item = prescription.item

    return {
        "prescription_id": prescription.prescription_id,
        "record_id": prescription.record_id,
        "item_id": prescription.item_id,
        "item_name": item.item_name if item else "Unknown item",
        "item_code": item.item_code if item else None,
        "category": item.category if item else None,
        "unit": item.unit if item else None,
        "quantity": prescription.quantity,
        "instructions": prescription.instructions or "",
        "status": prescription.status,
        "dispensed_quantity": prescription.dispensed_quantity or 0,
        "dispensed_by": prescription.dispensed_by,
        "dispensed_at": (
            prescription.dispensed_at.isoformat()
            if prescription.dispensed_at else None
        ),
        "created_at": (
            prescription.created_at.isoformat()
            if prescription.created_at else None
        ),
        "updated_at": (
            prescription.updated_at.isoformat()
            if prescription.updated_at else None
        ),
    }


# ============================================================
# GET PRESCRIPTIONS FOR A MEDICAL RECORD
# ============================================================

@router.get("/record/{record_id}")
def get_record_prescriptions(
    record_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    if current_user.role not in ["doctor", "nurse"]:
        raise HTTPException(
            status_code=403,
            detail="You are not allowed to view prescriptions."
        )

    record = db.query(MedicalRecord).filter(
        MedicalRecord.record_id == record_id
    ).first()

    if not record:
        raise HTTPException(
            status_code=404,
            detail="Medical record not found."
        )

    prescriptions = db.query(
        MedicalRecordPrescription
    ).filter(
        MedicalRecordPrescription.record_id == record_id
    ).order_by(
        MedicalRecordPrescription.prescription_id.asc()
    ).all()

    return [
        prescription_to_dict(p)
        for p in prescriptions
    ]


# ============================================================
# GET PRESCRIPTIONS FOR A PATIENT
# ============================================================

@router.get("/patient/{patient_id}")
def get_patient_prescriptions(
    patient_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    if current_user.role not in ["doctor", "nurse"]:
        raise HTTPException(
            status_code=403,
            detail="You are not allowed to view prescriptions."
        )

    patient = db.query(Patient).filter(
        Patient.patient_id == patient_id
    ).first()

    if not patient:
        raise HTTPException(
            status_code=404,
            detail="Patient not found."
        )

    prescriptions = db.query(
        MedicalRecordPrescription
    ).join(
        MedicalRecord,
        MedicalRecord.record_id ==
        MedicalRecordPrescription.record_id
    ).filter(
        MedicalRecord.patient_id == patient_id
    ).order_by(
        MedicalRecordPrescription.created_at.desc()
    ).all()

    return [
        prescription_to_dict(p)
        for p in prescriptions
    ]


# ============================================================
# CREATE PRESCRIPTION — DOCTOR ONLY
# ============================================================

@router.post("/")
async def create_prescription(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    require_doctor(current_user)

    body = await request.json()

    record_id = body.get("record_id")
    item_id = body.get("item_id")
    quantity = body.get("quantity")
    instructions = body.get("instructions")

    if not record_id:
        raise HTTPException(
            status_code=400,
            detail="record_id is required."
        )

    if not item_id:
        raise HTTPException(
            status_code=400,
            detail="item_id is required."
        )

    try:
        quantity = int(quantity)
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=400,
            detail="Quantity must be a valid number."
        )

    if quantity <= 0:
        raise HTTPException(
            status_code=400,
            detail="Quantity must be greater than 0."
        )

    record = db.query(MedicalRecord).filter(
        MedicalRecord.record_id == record_id
    ).first()

    if not record:
        raise HTTPException(
            status_code=404,
            detail="Medical record not found."
        )

    item = db.query(InventoryItem).filter(
        InventoryItem.item_id == item_id,
        InventoryItem.is_active == True
    ).first()

    if not item:
        raise HTTPException(
            status_code=404,
            detail="Inventory item not found or inactive."
        )

    prescription = MedicalRecordPrescription(
        record_id=record_id,
        item_id=item_id,
        quantity=quantity,
        instructions=instructions,
        status="pending",
        dispensed_quantity=0
    )

    db.add(prescription)

    try:
        db.commit()
        db.refresh(prescription)

        await manager.broadcast_event(
            "prescription_created",
            "prescription",
            "created",
            prescription_id=prescription.prescription_id,
            record_id=prescription.record_id,
            patient_id=prescription.patient_id,
            item_id=prescription.item_id,
            quantity=prescription.quantity,
            user_id=current_user.user_id)
    except Exception:
        db.rollback()

        raise HTTPException(
            status_code=500,
            detail="Failed to save prescription."
        )

    return {
        "message": "Prescription created successfully.",
        "prescription": prescription_to_dict(prescription)
    }


# ============================================================
# DISPENSE PRESCRIPTION — NURSE ONLY
# ============================================================

@router.post("/{prescription_id}/dispense")
async def dispense_prescription(
    prescription_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    require_nurse(current_user)

    body = await request.json()

    quantity = body.get("quantity")

    try:
        quantity = int(quantity)
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=400,
            detail="Quantity must be a valid number."
        )

    if quantity <= 0:
        raise HTTPException(
            status_code=400,
            detail="Dispense quantity must be greater than 0."
        )

    prescription = db.query(
        MedicalRecordPrescription
    ).filter(
        MedicalRecordPrescription.prescription_id ==
        prescription_id
    ).first()

    if not prescription:
        raise HTTPException(
            status_code=404,
            detail="Prescription not found."
        )

    if prescription.status == "cancelled":
        raise HTTPException(
            status_code=400,
            detail="This prescription has been cancelled."
        )

    if prescription.status == "dispensed":
        raise HTTPException(
            status_code=400,
            detail="This prescription has already been fully dispensed."
        )

    already_dispensed = prescription.dispensed_quantity or 0

    remaining_quantity = (
        prescription.quantity -
        already_dispensed
    )

    if quantity > remaining_quantity:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Cannot dispense {quantity}. "
                f"Only {remaining_quantity} remaining."
            )
        )

    item = db.query(InventoryItem).filter(
        InventoryItem.item_id == prescription.item_id,
        InventoryItem.is_active == True
    ).first()

    if not item:
        raise HTTPException(
            status_code=404,
            detail="Inventory item not found or inactive."
        )

    stock_before = item.current_stock or 0

    if stock_before < quantity:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Insufficient stock for {item.item_name}. "
                f"Available: {stock_before}, "
                f"Requested: {quantity}."
            )
        )

    stock_after = stock_before - quantity

    item.current_stock = stock_after

    new_dispensed_quantity = (
        already_dispensed + quantity
    )

    prescription.dispensed_quantity = (
        new_dispensed_quantity
    )

    prescription.dispensed_by = current_user.user_id
    prescription.dispensed_at = datetime.utcnow()

    if new_dispensed_quantity >= prescription.quantity:
        prescription.status = "dispensed"

    transaction = InventoryTransaction(
        item_id=item.item_id,
        transaction_type="dispense",
        quantity=quantity,
        stock_before=stock_before,
        stock_after=stock_after,
        remarks=(
            f"Dispensed for prescription "
            f"#{prescription.prescription_id}"
        ),
        user_id=current_user.user_id
    )

    db.add(transaction)

    try:
        db.commit()
        await manager.broadcast_event(
            "prescription_dispensed",
            "prescription",
            "dispensed",
            prescription_id=prescription.prescription_id,
            record_id=prescription.record_id,
            patient_id=prescription.patient_id,
            item_id=prescription.item_id,
            quantity=dispense_quantity,
            user_id=current_user.user_id
        )

        await manager.broadcast_event(
            "inventory_stock_updated",
            "inventory",
            "stock_updated",
            item_id=item.item_id,
            current_stock=item.current_stock,
            user_id=current_user.user_id
        )

        db.refresh(prescription)
        db.refresh(item)
        db.refresh(transaction)

    except Exception:
        db.rollback()

        raise HTTPException(
            status_code=500,
            detail="Failed to dispense prescription."
        )

    return {
        "message": "Prescription dispensed successfully.",

        "prescription": prescription_to_dict(
            prescription
        ),

        "inventory": {
            "item_id": item.item_id,
            "item_name": item.item_name,
            "stock_before": stock_before,
            "stock_after": stock_after,
            "dispensed_quantity": quantity
        },

        "transaction": {
            "transaction_id": transaction.transaction_id,
            "transaction_type": transaction.transaction_type,
            "quantity": transaction.quantity
        }
    }
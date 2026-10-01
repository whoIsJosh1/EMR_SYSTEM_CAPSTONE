import csv
import io
from datetime import datetime
from websocket_manager import manager
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from database import get_db
from models.models import InventoryItem, InventoryTransaction


router = APIRouter(
    prefix="/api/inventory",
    tags=["Inventory"]
)


# ============================================================
# HELPERS
# ============================================================

def item_to_dict(item):
    return {
        "item_id": item.item_id,
        "item_code": item.item_code,
        "item_name": item.item_name,
        "category": item.category,
        "unit": item.unit,
        "current_stock": item.current_stock,
        "reorder_level": item.reorder_level,
        "batch_number": item.batch_number,
        "expiration_date": (
            item.expiration_date.isoformat()
            if item.expiration_date else None
        ),
        "is_active": item.is_active,
        "created_at": (
            item.created_at.isoformat()
            if item.created_at else None
        ),
        "updated_at": (
            item.updated_at.isoformat()
            if item.updated_at else None
        )
    }


def transaction_to_dict(transaction):
    return {
        "transaction_id": transaction.transaction_id,
        "item_id": transaction.item_id,
        "item_name": (
            transaction.item.item_name
            if transaction.item else "Unknown"
        ),
        "transaction_type": transaction.transaction_type,
        "quantity": transaction.quantity,
        "stock_before": transaction.stock_before,
        "stock_after": transaction.stock_after,
        "remarks": transaction.remarks,
        "created_at": (
            transaction.created_at.isoformat()
            if transaction.created_at else None
        )
    }


# ============================================================
# GET ALL INVENTORY ITEMS
# ============================================================

@router.get("")
def get_inventory(db: Session = Depends(get_db)):
    items = (
        db.query(InventoryItem)
        .filter(InventoryItem.is_active == True)
        .order_by(InventoryItem.item_name.asc())
        .all()
    )

    return [item_to_dict(item) for item in items]


# ============================================================
# IMPORT INVENTORY FROM CSV
# CSV IMPORT ADDS ITEM DETAILS + INITIAL STOCK
# ============================================================

@router.post("/import-csv")
async def import_inventory_csv(
    file: UploadFile = File(...),
    db: Session = Depends(get_db)
):
    if not file.filename.lower().endswith(".csv"):
        raise HTTPException(
            status_code=400,
            detail="Please upload a CSV file."
        )

    content = await file.read()

    try:
        text = content.decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(text))
    except Exception:
        raise HTTPException(
            status_code=400,
            detail="Unable to read CSV file."
        )

    required_columns = {
        "item_code",
        "item_name",
        "category",
        "unit",
        "current_stock"
    }

    if not reader.fieldnames or not required_columns.issubset(
        set(reader.fieldnames)
    ):
        raise HTTPException(
            status_code=400,
            detail=(
                "CSV must include: item_code, item_name, "
                "category, unit, current_stock. "
                "Optional: reorder_level, batch_number, expiration_date."
            )
        )

    valid_categories = {
        "Medicine",
        "Vaccine",
        "Medical Supply"
    }

    imported = 0
    skipped = []

    try:
        for row_number, row in enumerate(reader, start=2):
            code = (row.get("item_code") or "").strip()
            name = (row.get("item_name") or "").strip()
            category = (row.get("category") or "").strip()
            unit = (row.get("unit") or "").strip()

            if not code or not name or not unit:
                skipped.append(
                    f"Row {row_number}: Missing required item details."
                )
                continue

            if category not in valid_categories:
                skipped.append(
                    f"Row {row_number}: Invalid category."
                )
                continue

            try:
                quantity = int(row.get("current_stock", "0"))
                reorder_level = int(row.get("reorder_level") or 10)
            except (ValueError, TypeError):
                skipped.append(
                    f"Row {row_number}: Stock and reorder level "
                    "must be whole numbers."
                )
                continue

            if quantity < 0 or reorder_level < 0:
                skipped.append(
                    f"Row {row_number}: Quantities cannot be negative."
                )
                continue

            existing = (
                db.query(InventoryItem)
                .filter(InventoryItem.item_code == code)
                .first()
            )

            if existing:
                skipped.append(
                    f"Row {row_number}: Item code '{code}' already exists."
                )
                continue

            expiration_date = None
            expiration_text = (
                row.get("expiration_date") or ""
            ).strip()

            if expiration_text:
                try:
                    expiration_date = datetime.strptime(
                        expiration_text, "%Y-%m-%d"
                    ).date()
                except ValueError:
                    skipped.append(
                        f"Row {row_number}: expiration_date must use "
                        "YYYY-MM-DD format."
                    )
                    continue

            item = InventoryItem(
                item_code=code,
                item_name=name,
                category=category,
                unit=unit,
                current_stock=quantity,
                reorder_level=reorder_level,
                batch_number=(
                    row.get("batch_number") or ""
                ).strip() or None,
                expiration_date=expiration_date,
                is_active=True
            )

            db.add(item)
            db.flush()

            if quantity > 0:
                transaction = InventoryTransaction(
                    item_id=item.item_id,
                    transaction_type="import",
                    quantity=quantity,
                    stock_before=0,
                    stock_after=quantity,
                    remarks="Initial stock from CSV import"
                )
                db.add(transaction)

            imported += 1

        db.commit()

    except Exception as error:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"CSV import failed: {str(error)}"
        )

    return {
        "message": "CSV import completed.",
        "imported": imported,
        "skipped_count": len(skipped),
        "skipped_rows": skipped
    }


# ============================================================
# RECEIVE STOCK
# ============================================================

@router.post("/receive-stock/{item_id}")
async def receive_stock(
    item_id: int,
    quantity: int,
    remarks: str = "",
    db: Session = Depends(get_db)
):
    if quantity <= 0:
        raise HTTPException(
            status_code=400,
            detail="Quantity must be greater than zero."
        )

    item = (
        db.query(InventoryItem)
        .filter(
            InventoryItem.item_id == item_id,
            InventoryItem.is_active == True
        )
        .first()
    )

    if not item:
        raise HTTPException(
            status_code=404,
            detail="Inventory item not found."
        )

    stock_before = item.current_stock
    item.current_stock += quantity

    transaction = InventoryTransaction(
        item_id=item.item_id,
        transaction_type="stock_in",
        quantity=quantity,
        stock_before=stock_before,
        stock_after=item.current_stock,
        remarks=remarks or "Stock received"
    )

    db.add(transaction)
    db.commit()
    db.refresh(item)

    await manager.broadcast_event(
        "inventory_stock_updated",
        "inventory",
        "stock_updated",
        item_id=item.item_id,
        item_name=item.item_name,
        current_stock=item.current_stock
    )

    return {
        "message": "Stock received successfully.",
        "item": item_to_dict(item)
    }


# ============================================================
# GET TRANSACTION HISTORY
# ============================================================

@router.get("/transactions")
def get_inventory_transactions(
    db: Session = Depends(get_db)
):
    transactions = (
        db.query(InventoryTransaction)
        .order_by(
            InventoryTransaction.created_at.desc(),
            InventoryTransaction.transaction_id.desc()
        )
        .limit(500)
        .all()
    )

    return [
        transaction_to_dict(transaction)
        for transaction in transactions
    ]
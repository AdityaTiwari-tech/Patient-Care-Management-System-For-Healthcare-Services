"""
services/order_service.py
Patient-facing direct medicine purchases (the "Pharmacy" tab), as opposed
to services/prescription_service.py which handles a doctor prescribing
medicines to a patient. Both reduce Medicine.stock_quantity, but they are
two independent flows with two independent tables (MedicineOrder /
MedicineOrderItem vs Prescription / PrescriptionItem) — a purchase is not
a clinical event and shouldn't be mixed into a doctor's prescription
history, or vice versa.

Payment is entirely simulated in views/pharmacy_view.py (a fake "Pay now"
step with no real gateway). place_order() is called only AFTER that step
reports success — its job is purely to re-validate stock and write the
order atomically, exactly the same "trust nothing the UI already checked"
pattern appointment_service.book_appointment() uses for slot booking.
"""
from datetime import datetime
from typing import List, Tuple

from core.database import get_session
from models.models import Medicine, MedicineOrder, MedicineOrderItem


class OrderError(Exception):
    pass


def place_order(patient_id: int, cart: dict) -> Tuple[int, float]:
    """
    cart is {medicine_id: quantity}. Every line is re-checked against
    live stock inside one transaction — if any line is invalid (medicine
    gone, deactivated, or not enough stock left), NOTHING is written and
    NOTHING is decremented; the whole order fails together.

    Returns (order_id, total_amount).
    """
    if not cart:
        raise OrderError("Your cart is empty.")

    with get_session() as db:
        order = MedicineOrder(
            patient_id=patient_id, status="paid",
            total_amount=0, created_at=datetime.utcnow(),
        )
        db.add(order)
        db.flush()  # get order.id for the item rows

        total = 0.0
        lines_written = 0
        for medicine_id, quantity in cart.items():
            quantity = int(quantity)
            if quantity <= 0:
                continue

            medicine = db.get(Medicine, medicine_id)
            if not medicine or not medicine.is_active:
                raise OrderError("One of the items in your cart is no longer available.")
            if medicine.stock_quantity < quantity:
                raise OrderError(
                    f"Only {medicine.stock_quantity} unit(s) of {medicine.name} left in stock — "
                    "please update the quantity in your cart."
                )

            line_total = float(medicine.price) * quantity
            total += line_total
            medicine.stock_quantity -= quantity
            db.add(MedicineOrderItem(
                order_id=order.id, medicine_id=medicine.id,
                medicine_name=medicine.name, unit_price=medicine.price,
                quantity=quantity, line_total=line_total,
            ))
            lines_written += 1

        if lines_written == 0:
            raise OrderError("Your cart is empty.")

        order.total_amount = total
        db.flush()
        return order.id, total


def list_patient_orders(patient_id: int) -> List[dict]:
    """Newest-first order history with items nested — for the patient's
    'My orders' tab in views/pharmacy_view.py."""
    with get_session() as db:
        orders = (
            db.query(MedicineOrder)
            .filter(MedicineOrder.patient_id == patient_id)
            .order_by(MedicineOrder.created_at.desc())
            .all()
        )
        result = []
        for o in orders:
            items = (
                db.query(MedicineOrderItem)
                .filter(MedicineOrderItem.order_id == o.id)
                .all()
            )
            result.append({
                "id": o.id,
                "status": o.status,
                "total_amount": float(o.total_amount),
                "created_at": o.created_at,
                "items": [
                    {
                        "medicine_name": it.medicine_name,
                        "quantity": it.quantity,
                        "unit_price": float(it.unit_price),
                        "line_total": float(it.line_total),
                    }
                    for it in items
                ],
            })
        return result
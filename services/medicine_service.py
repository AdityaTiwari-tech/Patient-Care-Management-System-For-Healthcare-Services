"""
services/medicine_service.py
The pharmacy catalog the admin manages: CRUD over the `medicines` table.

Image handling: an image is stored as a *reference* string, never as
bytes — either an http(s) URL (imported from the web) or a path relative
to the project root pointing into assets/medicines/ (uploaded from the
admin's computer). save_image_file() writes an uploaded file to disk and
returns that relative path; the UI's image_ref_for_display() turns either
kind of reference back into something st.image() can render.

Every function returns plain dicts built INSIDE the session, so callers
never hit DetachedInstanceError after the session commits/closes.

Specialty: medicines can optionally be tagged with a Specialty (the same
table doctors use) so the admin catalog and the patient's pharmacy Shop
tab can both be filtered by it — e.g. narrowing to "Cardiology" medicines.
A medicine with no specialty_id is treated as "General" everywhere it's
displayed; the column is nullable on purpose since not every medicine
(antibiotics, painkillers) maps cleanly onto one clinical specialty.
"""
import base64
import mimetypes
import os
import uuid
from typing import List, Optional

from core.database import get_session
from models.models import Medicine, Specialty

# assets/medicines/ under the project root (…/services/ -> up one level).
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_IMAGE_DIR = os.path.join(_PROJECT_ROOT, "assets", "medicines")

LOW_STOCK_THRESHOLD = 10

# Sentinel distinguishing "caller didn't pass specialty_id, leave it alone"
# from "caller explicitly wants it set to None (i.e. General)" — every
# other Optional field here uses None itself for "no change", but that
# doesn't work for specialty_id since None IS a valid value to set.
_UNSET = object()


class MedicineError(Exception):
    pass


def _to_dict(m: Medicine, specialty: Optional[Specialty] = None) -> dict:
    return {
        "id": m.id,
        "name": m.name,
        "description": m.description or "",
        "price": float(m.price) if m.price is not None else 0.0,
        "stock_quantity": m.stock_quantity or 0,
        "image_path": m.image_path or "",
        "is_active": bool(m.is_active),
        "low_stock": (m.stock_quantity or 0) <= LOW_STOCK_THRESHOLD,
        "specialty_id": m.specialty_id,
        "specialty": specialty.name if specialty else "General",
    }


def save_image_file(uploaded_file) -> str:
    """Write a Streamlit UploadedFile to assets/medicines/ and return a
    project-relative path like 'assets/medicines/ab12cd.png'. Kept relative
    so the DB reference stays portable if the project folder moves."""
    os.makedirs(_IMAGE_DIR, exist_ok=True)
    _, ext = os.path.splitext(uploaded_file.name)
    ext = (ext or ".png").lower()
    fname = f"{uuid.uuid4().hex}{ext}"
    abs_path = os.path.join(_IMAGE_DIR, fname)
    with open(abs_path, "wb") as f:
        f.write(uploaded_file.getbuffer())
    return os.path.join("assets", "medicines", fname).replace("\\", "/")


def image_ref_for_display(image_path: str) -> Optional[str]:
    """Turn a stored reference into something st.image() accepts: a URL is
    returned as-is; a relative path is resolved to an absolute file path.
    Returns None if there's nothing to show or the file is missing."""
    if not image_path:
        return None
    if image_path.startswith(("http://", "https://")):
        return image_path
    abs_path = os.path.join(_PROJECT_ROOT, image_path)
    return abs_path if os.path.exists(abs_path) else None


def image_src_for_html(image_path: str) -> Optional[str]:
    """Like image_ref_for_display(), but for an HTML <img src=…>: a URL is
    returned as-is; a local file is inlined as a base64 data URI so the
    browser can load it (Streamlit doesn't serve arbitrary disk paths)."""
    ref = image_ref_for_display(image_path)
    if ref is None or ref.startswith(("http://", "https://")):
        return ref
    mime = mimetypes.guess_type(ref)[0] or "image/png"
    try:
        with open(ref, "rb") as f:
            encoded = base64.b64encode(f.read()).decode("ascii")
    except OSError:
        return None
    return f"data:{mime};base64,{encoded}"


def list_medicines(
    active_only: bool = True, search: str = "", specialty_id: Optional[int] = None,
) -> List[dict]:
    with get_session() as db:
        q = db.query(Medicine, Specialty).outerjoin(
            Specialty, Medicine.specialty_id == Specialty.id
        )
        if active_only:
            q = q.filter(Medicine.is_active.is_(True))
        if search:
            q = q.filter(Medicine.name.ilike(f"%{search.lower()}%"))
        if specialty_id:
            q = q.filter(Medicine.specialty_id == specialty_id)
        rows = q.order_by(Medicine.name).all()
        return [_to_dict(m, s) for m, s in rows]


def get_medicine(medicine_id: int) -> Optional[dict]:
    with get_session() as db:
        row = (
            db.query(Medicine, Specialty)
            .outerjoin(Specialty, Medicine.specialty_id == Specialty.id)
            .filter(Medicine.id == medicine_id)
            .first()
        )
        return _to_dict(*row) if row else None


def add_medicine(
    name: str, description: str = "", price: float = 0.0,
    stock_quantity: int = 0, image_path: str = "",
    specialty_id: Optional[int] = None,
) -> int:
    if not name or not name.strip():
        raise MedicineError("Medicine name is required.")
    with get_session() as db:
        m = Medicine(
            name=name.strip(), description=description.strip(),
            price=price or 0, stock_quantity=int(stock_quantity or 0),
            image_path=image_path or None, is_active=True,
            specialty_id=specialty_id,
        )
        db.add(m)
        db.flush()
        return m.id


def update_medicine(
    medicine_id: int, name: Optional[str] = None, description: Optional[str] = None,
    price: Optional[float] = None, stock_quantity: Optional[int] = None,
    image_path: Optional[str] = None, specialty_id=_UNSET,
) -> None:
    """specialty_id defaults to the _UNSET sentinel (not None) so callers
    can explicitly clear a medicine back to "General" by passing
    specialty_id=None, while simply omitting the argument still means
    "don't touch it" — see _UNSET above."""
    with get_session() as db:
        m = db.get(Medicine, medicine_id)
        if not m:
            raise MedicineError("Medicine not found.")
        if name is not None:
            m.name = name.strip()
        if description is not None:
            m.description = description.strip()
        if price is not None:
            m.price = price
        if stock_quantity is not None:
            m.stock_quantity = int(stock_quantity)
        if image_path is not None:
            m.image_path = image_path or None
        if specialty_id is not _UNSET:
            m.specialty_id = specialty_id


def adjust_stock(medicine_id: int, delta: int) -> None:
    """Add (delta>0) or remove (delta<0) units. Clamps at zero so stock can
    never go negative — the caller (prescription_service) checks sufficiency
    first, this is just a floor."""
    with get_session() as db:
        m = db.get(Medicine, medicine_id)
        if not m:
            raise MedicineError("Medicine not found.")
        m.stock_quantity = max(0, (m.stock_quantity or 0) + delta)


def deactivate_medicine(medicine_id: int) -> None:
    """Soft-delete: hides the medicine from the catalog but keeps it for any
    prescription that already references it."""
    with get_session() as db:
        m = db.get(Medicine, medicine_id)
        if m:
            m.is_active = False
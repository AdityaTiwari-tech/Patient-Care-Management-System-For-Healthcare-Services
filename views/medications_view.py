"""
views/medications_view.py
Admin's pharmacy inventory: browse the medicine catalog, add new medicines
(with an image from the web or the computer, and an optional specialty
tag), and adjust price / stock. Rendered from admin_portal.py when the
"Medications" nav item is chosen.
"""
import streamlit as st

from services import medicine_service
from services.medicine_service import MedicineError
from services.doctor_service import list_specialties
from views.components import ecg_divider, kpi_tile


def render():
    st.markdown("### 💊 Medications & inventory")
    ecg_divider()

    medicines = medicine_service.list_medicines(active_only=True)
    _summary(medicines)

    with st.expander("➕ Add a new medicine", expanded=not medicines):
        _add_medicine_form()

    st.markdown("---")
    _catalog()


def _summary(medicines: list[dict]):
    total = len(medicines)
    stock_units = sum(m["stock_quantity"] for m in medicines)
    stock_value = sum(m["price"] * m["stock_quantity"] for m in medicines)
    low = sum(1 for m in medicines if m["low_stock"])

    c1, c2, c3, c4 = st.columns(4)
    kpi_tile("Medicines", total, c1)
    kpi_tile("Units in stock", stock_units, c2, dark=True)
    kpi_tile("Inventory value", f"₹{stock_value:,.0f}", c3, dark=True)
    kpi_tile(
        "Low stock", low, c4, caption="needs restock" if low else "all stocked",
        variant="accent" if low else None,
    )


def _specialty_options() -> tuple[list[str], dict]:
    """Returns (dropdown labels incl. 'General', {label: specialty_id})."""
    specs = list_specialties()
    labels = ["General"] + [s["name"] for s in specs]
    id_by_label = {s["name"]: s["id"] for s in specs}
    return labels, id_by_label


def _add_medicine_form():
    name = st.text_input("Medicine name", placeholder="e.g. Atorvastatin", key="med_add_name")
    description = st.text_input(
        "Form / strength", placeholder="e.g. Tablet 20mg", key="med_add_desc"
    )
    labels, id_by_label = _specialty_options()
    c1, c2 = st.columns(2)
    with c1:
        price = st.number_input("Price per unit (₹)", 0.0, 100000.0, 0.0, step=1.0, key="med_add_price")
    with c2:
        stock = st.number_input("Initial stock (units)", 0, 1000000, 0, step=1, key="med_add_stock")
    specialty_label = st.selectbox(
        "Specialty", labels, key="med_add_specialty",
        help="Which specialty this medicine is typically prescribed under — lets patients and admins filter the catalog by it.",
    )

    st.caption("Medicine image (optional)")
    source = st.radio(
        "Image source", ["None", "Upload from computer", "From web URL"],
        horizontal=True, key="med_add_img_source", label_visibility="collapsed",
    )
    uploaded = None
    web_url = ""
    if source == "Upload from computer":
        uploaded = st.file_uploader(
            "Choose an image", type=["png", "jpg", "jpeg", "webp"], key="med_add_upload"
        )
    elif source == "From web URL":
        web_url = st.text_input("Image URL", placeholder="https://…", key="med_add_url")

    if st.button("Add medicine", type="primary", key="med_add_btn"):
        if not name.strip():
            st.error("Medicine name is required.")
            return
        try:
            image_path = ""
            if source == "Upload from computer" and uploaded is not None:
                image_path = medicine_service.save_image_file(uploaded)
            elif source == "From web URL" and web_url.strip():
                image_path = web_url.strip()
            medicine_service.add_medicine(
                name=name, description=description, price=price,
                stock_quantity=stock, image_path=image_path,
                specialty_id=id_by_label.get(specialty_label),
            )
            st.success(f"Added {name}.")
            st.rerun()
        except MedicineError as e:
            st.error(str(e))


def _catalog():
    st.markdown("**Catalog**")
    labels, id_by_label = _specialty_options()

    c1, c2 = st.columns([2, 1])
    with c1:
        search = st.text_input("Search medicines", placeholder="Name", key="med_search")
    with c2:
        spec_filter = st.selectbox("Filter by specialty", ["All specialties"] + labels, key="med_spec_filter")

    specialty_id = None
    if spec_filter not in ("All specialties", "General"):
        specialty_id = id_by_label.get(spec_filter)

    medicines = medicine_service.list_medicines(active_only=True, search=search, specialty_id=specialty_id)
    if spec_filter == "General":
        medicines = [m for m in medicines if m["specialty_id"] is None]

    if not medicines:
        st.info("No medicines match this filter yet.")
        return

    # 3-across card grid
    for row_start in range(0, len(medicines), 3):
        cols = st.columns(3)
        for col, m in zip(cols, medicines[row_start:row_start + 3]):
            with col:
                with st.container(border=True):
                    src = medicine_service.image_src_for_html(m["image_path"])
                    if src:
                        st.markdown(
                            f'<div class="med-img-frame"><img src="{src}" alt=""></div>',
                            unsafe_allow_html=True,
                        )
                    else:
                        st.markdown(
                            '<div class="med-img-frame"><span class="med-emoji">💊</span></div>',
                            unsafe_allow_html=True,
                        )
                    stock = m["stock_quantity"]
                    pill_cls = "is-out" if stock == 0 else ("is-low" if m["low_stock"] else "")
                    pill_txt = "Out of stock" if stock == 0 else f"{stock} in stock"
                    st.markdown(
                        f"""<p class="med-name">{m['name']}</p>
                        <p class="med-desc">{m['description'] or '&nbsp;'}</p>
                        <span class="pill">{m['specialty']}</span>
                        <span class="med-price">₹{m['price']:.2f}</span>
                        <span class="med-stock-pill {pill_cls}">{pill_txt}</span>""",
                        unsafe_allow_html=True,
                    )
                    with st.expander("Edit / restock"):
                        _edit_medicine(m)


def _edit_medicine(m: dict):
    mid = m["id"]
    labels, id_by_label = _specialty_options()
    current_label = m["specialty"] if m["specialty"] in labels else "General"

    c1, c2 = st.columns(2)
    with c1:
        new_price = st.number_input(
            "Price per unit (₹)", 0.0, 100000.0, float(m["price"]), step=1.0,
            key=f"med_price_{mid}",
        )
    with c2:
        add_units = st.number_input(
            "Add stock (units)", 0, 1000000, 0, step=1, key=f"med_restock_{mid}"
        )
    new_specialty_label = st.selectbox(
        "Specialty", labels, index=labels.index(current_label), key=f"med_specialty_{mid}",
    )

    b1, b2 = st.columns(2)
    if b1.button("Save changes", key=f"med_save_{mid}"):
        try:
            medicine_service.update_medicine(
                mid, price=new_price,
                specialty_id=id_by_label.get(new_specialty_label),
            )
            if add_units:
                medicine_service.adjust_stock(mid, int(add_units))
            st.success("Updated.")
            st.rerun()
        except MedicineError as e:
            st.error(str(e))
    if b2.button("Remove from catalog", key=f"med_del_{mid}"):
        medicine_service.deactivate_medicine(mid)
        st.rerun()
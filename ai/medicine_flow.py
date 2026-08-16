"""
ai/medicine_flow.py
Deterministic, human-confirmed medicine CRUD for the admin chatbot —
mirrors ai/booking_flow.py's design exactly. The LLM has no tool that
writes to the medicines table; it can only notice intent (add/update/
remove a medicine) and best-guess the fields from free text via a
"propose_*" tool in ai/smartcare_agent.py, which sets a signal and
pre-fills this wizard. A human admin then reviews the REAL form fields —
the same ones views/medications_view.py uses — and clicks Save/Remove
themselves before anything is written.

    IDLE -> ADD_REVIEW | EDIT_REVIEW | DELETE_REVIEW -> DONE

Two guarantees this buys, same as booking_flow.py:
  1. The LLM cannot silently corrupt price, stock, or the catalog — every
     write still goes through medicine_service, only after a real click.
  2. If the LLM matched the wrong medicine or guessed a wrong price, the
     admin sees it pre-filled and can just fix it before saving — nothing
     is written until they do.
"""
import streamlit as st

from services import medicine_service
from services.medicine_service import MedicineError
from services.doctor_service import list_specialties

STATE_KEY = "medicine_wizard_state"
DATA_KEY = "medicine_wizard_data"
RESULT_KEY = "medicine_wizard_result_msg"


def open_add(data: dict):
    """Called by chatbot_view.py when signals["open_medicine_form"] comes
    back with mode="add" from propose_add_medicine."""
    st.session_state[STATE_KEY] = "ADD_REVIEW"
    st.session_state[DATA_KEY] = data


def open_edit(data: dict):
    st.session_state[STATE_KEY] = "EDIT_REVIEW"
    st.session_state[DATA_KEY] = data


def open_delete(data: dict):
    st.session_state[STATE_KEY] = "DELETE_REVIEW"
    st.session_state[DATA_KEY] = data


def is_active() -> bool:
    return st.session_state.get(STATE_KEY, "IDLE") not in ("IDLE", "DONE")


def render():
    """Draws whichever step is active. chatbot_view.py calls this once
    per rerun, right after the chat history, whenever is_active()."""
    state = st.session_state.get(STATE_KEY, "IDLE")
    data = st.session_state.setdefault(DATA_KEY, {})

    with st.container(border=True):
        st.markdown("**💊 Medicine assistant**")
        if state == "ADD_REVIEW":
            _review_add(data)
        elif state == "EDIT_REVIEW":
            _review_edit(data)
        elif state == "DELETE_REVIEW":
            _review_delete(data)
        elif state == "DONE":
            _done()


def _specialty_options():
    specs = list_specialties()
    labels = ["General"] + [s["name"] for s in specs]
    id_by_label = {s["name"]: s["id"] for s in specs}
    return labels, id_by_label


def _review_add(data: dict):
    st.caption("The assistant prepared this from your message — review before saving.")
    name = st.text_input("Medicine name", value=data.get("name", ""), key="mw_add_name")
    description = st.text_input("Form / strength", value=data.get("description", ""), key="mw_add_desc")

    labels, id_by_label = _specialty_options()
    default_label = data.get("specialty") if data.get("specialty") in labels else "General"

    c1, c2 = st.columns(2)
    with c1:
        price = st.number_input(
            "Price per unit (₹)", 0.0, 100000.0, float(data.get("price") or 0.0),
            step=1.0, key="mw_add_price",
        )
    with c2:
        stock = st.number_input(
            "Initial stock (units)", 0, 1000000, int(data.get("stock_quantity") or 0),
            step=1, key="mw_add_stock",
        )
    specialty_label = st.selectbox(
        "Specialty", labels, index=labels.index(default_label), key="mw_add_specialty",
    )

    st.caption("Medicine image (optional)")
    source = st.radio(
        "Image source", ["None", "Upload from computer", "From web URL"],
        horizontal=True, key="mw_add_img_source", label_visibility="collapsed",
    )
    uploaded = None
    web_url = ""
    if source == "Upload from computer":
        uploaded = st.file_uploader(
            "Choose an image", type=["png", "jpg", "jpeg", "webp"], key="mw_add_upload"
        )
    elif source == "From web URL":
        web_url = st.text_input("Image URL", placeholder="https://…", key="mw_add_url")

    c3, c4 = st.columns(2)
    if c3.button("💾 Save medicine", type="primary", key="mw_add_save"):
        if not name.strip():
            st.error("Medicine name is required.")
        else:
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
                st.session_state[RESULT_KEY] = f"Added {name} to the catalog."
                st.session_state[STATE_KEY] = "DONE"
                st.rerun()
            except MedicineError as e:
                st.error(str(e))
    if c4.button("Cancel", key="mw_add_cancel"):
        _reset()
        st.rerun()


def _review_edit(data: dict):
    medicine_id = data.get("medicine_id")
    current = medicine_service.get_medicine(medicine_id) if medicine_id else None
    if not current:
        st.error("Couldn't find that medicine anymore — it may have been removed.")
        if st.button("Close", key="mw_edit_close_missing"):
            _reset()
            st.rerun()
        return

    st.caption(f"The assistant matched this to **{current['name']}** — review before saving.")
    labels, id_by_label = _specialty_options()
    proposed_specialty = data.get("specialty")
    default_label = proposed_specialty if proposed_specialty in labels else (
        current["specialty"] if current["specialty"] in labels else "General"
    )

    c1, c2 = st.columns(2)
    with c1:
        new_price = st.number_input(
            "Price per unit (₹)", 0.0, 100000.0,
            float(data.get("price", current["price"]) or 0.0), step=1.0, key="mw_edit_price",
        )
    with c2:
        new_stock = st.number_input(
            "Set stock to (units)", 0, 1000000,
            int(data.get("stock_quantity", current["stock_quantity"]) or 0), step=1, key="mw_edit_stock",
            help="This SETS the stock count directly — it does not add units on top.",
        )
    specialty_label = st.selectbox(
        "Specialty", labels, index=labels.index(default_label), key="mw_edit_specialty",
    )

    c3, c4 = st.columns(2)
    if c3.button("💾 Save changes", type="primary", key="mw_edit_save"):
        try:
            medicine_service.update_medicine(
                medicine_id, price=new_price, stock_quantity=new_stock,
                specialty_id=id_by_label.get(specialty_label),
            )
            st.session_state[RESULT_KEY] = f"Updated {current['name']}."
            st.session_state[STATE_KEY] = "DONE"
            st.rerun()
        except MedicineError as e:
            st.error(str(e))
    if c4.button("Cancel", key="mw_edit_cancel"):
        _reset()
        st.rerun()


def _review_delete(data: dict):
    medicine_id = data.get("medicine_id")
    current = medicine_service.get_medicine(medicine_id) if medicine_id else None
    if not current:
        st.error("Couldn't find that medicine anymore — it may already be removed.")
        if st.button("Close", key="mw_del_close_missing"):
            _reset()
            st.rerun()
        return

    st.warning(
        f"Remove **{current['name']}** from the catalog? This hides it from the "
        "pharmacy and admin catalog but keeps it on any past prescription."
    )
    c1, c2 = st.columns(2)
    if c1.button("🗑 Confirm remove", type="primary", key="mw_del_confirm"):
        medicine_service.deactivate_medicine(medicine_id)
        st.session_state[RESULT_KEY] = f"Removed {current['name']} from the catalog."
        st.session_state[STATE_KEY] = "DONE"
        st.rerun()
    if c2.button("Cancel", key="mw_del_cancel"):
        _reset()
        st.rerun()


def _done():
    st.success(st.session_state.get(RESULT_KEY, "Done."))
    if st.button("Close", key="mw_close"):
        _reset()
        st.rerun()


def _reset():
    st.session_state[STATE_KEY] = "IDLE"
    st.session_state[DATA_KEY] = {}
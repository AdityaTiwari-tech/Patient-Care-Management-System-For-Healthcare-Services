"""
views/pharmacy_view.py
Patient-facing pharmacy: browse the catalog, build a cart, and check out
through a simulated "Pay now" step (no real payment gateway). All the
real work — stock validation, decrement, and order persistence — lives
in services/order_service.py; this file only renders and manages the
cart in st.session_state.

Reuses the .med-* CSS classes already defined for the admin's catalog
view (views/medications_view.py) so the two feel like the same product.
"""
import time
import streamlit as st

from services import medicine_service, order_service
from services.doctor_service import list_specialties
from services.order_service import OrderError
from views.components import button_tabs, ecg_divider

_CART_PREFIX = "pharmacy_cart"


def render(patient_id: int):
    st.markdown("### 💊 Pharmacy")
    st.caption("Browse the catalog and order your medicines online.")
    ecg_divider()

    cart_key = f"{_CART_PREFIX}_{patient_id}"
    if cart_key not in st.session_state:
        st.session_state[cart_key] = {}  # {medicine_id: quantity}

    cart_count = sum(st.session_state[cart_key].values())
    selected = button_tabs(
        [("shop", "🛒 Shop"), ("cart", f"🧺 Cart ({cart_count})"), ("orders", "📦 My orders")],
        key="pharmacy",
    )

    if selected == "shop":
        _shop(cart_key)
    elif selected == "cart":
        _cart(patient_id, cart_key)
    elif selected == "orders":
        _orders(patient_id)


def _shop(cart_key: str):
    specs = list_specialties()
    spec_options = ["All specialties", "General"] + [s["name"] for s in specs]
    id_by_name = {s["name"]: s["id"] for s in specs}

    c1, c2 = st.columns([2, 1])
    with c1:
        search = st.text_input("Search medicines", placeholder="e.g. Atorvastatin", key="pharm_search")
    with c2:
        chosen_spec = st.selectbox("Specialty", spec_options, key="pharm_spec_filter")

    specialty_id = None
    if chosen_spec not in ("All specialties", "General"):
        specialty_id = id_by_name.get(chosen_spec)

    medicines = medicine_service.list_medicines(active_only=True, search=search, specialty_id=specialty_id)
    if chosen_spec == "General":
        medicines = [m for m in medicines if m["specialty_id"] is None]

    if not medicines:
        no_filter = not search and chosen_spec == "All specialties"
        st.info("The pharmacy catalog is empty right now." if no_filter else "No medicines match this filter yet.")
        return

    cart = st.session_state[cart_key]
    for row_start in range(0, len(medicines), 3):
        cols = st.columns(3)
        for col, m in zip(cols, medicines[row_start:row_start + 3]):
            with col:
                with st.container(border=True):
                    _medicine_card(m, cart, cart_key)


def _medicine_card(m: dict, cart: dict, cart_key: str):
    src = medicine_service.image_src_for_html(m["image_path"])
    if src:
        st.markdown(f'<div class="med-img-frame"><img src="{src}" alt=""></div>', unsafe_allow_html=True)
    else:
        st.markdown('<div class="med-img-frame"><span class="med-emoji">💊</span></div>', unsafe_allow_html=True)

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

    already_in_cart = cart.get(m["id"], 0)
    room_left = stock - already_in_cart

    if stock == 0:
        st.button("Out of stock", key=f"pharm_add_{m['id']}", disabled=True, use_container_width=True)
        return
    if room_left <= 0:
        st.caption(f"All {stock} unit(s) already in your cart")
        return

    c1, c2 = st.columns([1, 1.6])
    qty = c1.number_input(
        "Qty", 1, room_left, 1, key=f"pharm_qty_{m['id']}", label_visibility="collapsed",
    )
    if c2.button("Add to cart", key=f"pharm_add_{m['id']}", type="primary", use_container_width=True):
        cart[m["id"]] = already_in_cart + int(qty)
        st.session_state[cart_key] = cart
        st.toast(f"Added {int(qty)} × {m['name']} to cart")
        st.rerun()


def _cart(patient_id: int, cart_key: str):
    cart = st.session_state[cart_key]
    if not cart:
        st.info("Your cart is empty — add medicines from the Shop tab.")
        return

    # Re-fetch the live catalog so prices/stock shown here are current,
    # never trusted from whatever was true when the item was added.
    catalog = {m["id"]: m for m in medicine_service.list_medicines(active_only=True)}
    subtotal = 0.0
    stale_ids = []

    for medicine_id, qty in list(cart.items()):
        m = catalog.get(medicine_id)
        if not m:
            stale_ids.append(medicine_id)
            continue
        subtotal += _cart_line(m, medicine_id, qty, cart, cart_key)

    if stale_ids:
        for mid in stale_ids:
            cart.pop(mid, None)
        st.session_state[cart_key] = cart
        st.warning("Some items in your cart are no longer available and were removed.")
        st.rerun()

    if not cart:
        return

    st.markdown("---")
    st.markdown(f"### Subtotal: ₹{subtotal:,.2f}")
    _checkout(patient_id, cart_key, subtotal)


def _cart_line(m: dict, medicine_id, qty: int, cart: dict, cart_key: str) -> float:
    line_total = m["price"] * qty
    with st.container(border=True):
        c1, c2, c3, c4 = st.columns([3, 1.2, 1.2, 1])
        c1.markdown(f"**{m['name']}**")
        c1.caption(m["description"] or "")
        cap = max(m["stock_quantity"], 1)
        new_qty = c2.number_input(
            "Qty", 1, cap, min(qty, cap),
            key=f"cart_qty_{medicine_id}", label_visibility="collapsed",
        )
        if int(new_qty) != qty:
            cart[medicine_id] = int(new_qty)
            st.session_state[cart_key] = cart
            st.rerun()
        c3.markdown(f"₹{line_total:,.2f}")
        if c4.button("Remove", key=f"cart_rm_{medicine_id}"):
            cart.pop(medicine_id, None)
            st.session_state[cart_key] = cart
            st.rerun()
    return line_total


def _checkout(patient_id: int, cart_key: str, subtotal: float):
    with st.expander("💳 Pay now", expanded=True):
        method = st.radio(
            "Payment method", ["UPI", "Card", "Net banking"],
            horizontal=True, key="pharm_pay_method",
        )
        if method == "UPI":
            st.text_input("UPI ID", placeholder="you@upi", key="pharm_upi")
        elif method == "Card":
            c1, c2 = st.columns(2)
            c1.text_input("Card number", placeholder="4242 4242 4242 4242", key="pharm_card")
            c2.text_input("Expiry / CVV", placeholder="12/28 · 123", key="pharm_card_exp")
        else:
            st.selectbox("Bank", ["HDFC", "ICICI", "SBI", "Axis"], key="pharm_bank")

        st.caption("Simulated payment for demo purposes — no real transaction is made and no card details are stored.")

        if st.button(f"Pay ₹{subtotal:,.2f}", type="primary", key="pharm_pay_btn", use_container_width=True):
            cart = dict(st.session_state[cart_key])
            with st.spinner("Processing payment…"):
                time.sleep(1.1)  # purely cosmetic — makes the fake payment feel real
            try:
                order_id, total = order_service.place_order(patient_id, cart)
                st.session_state[cart_key] = {}
                st.success(f"Payment successful — order #{order_id} placed for ₹{total:,.2f}.")
                st.balloons()
                st.rerun()
            except OrderError as e:
                st.error(str(e))


def _orders(patient_id: int):
    orders = order_service.list_patient_orders(patient_id)
    if not orders:
        st.info("No orders yet — anything you buy from the Shop tab will show up here.")
        return

    for o in orders:
        with st.container(border=True):
            c1, c2 = st.columns([3, 1])
            with c1:
                st.markdown(f"**Order #{o['id']}**")
                st.caption(o["created_at"].strftime("%d %b %Y, %I:%M %p"))
            with c2:
                st.markdown(f"**₹{o['total_amount']:,.2f}**")
                st.caption(o["status"].capitalize())
            for it in o["items"]:
                st.markdown(
                    f"- {it['medicine_name']} × {it['quantity']} — ₹{it['line_total']:,.2f}"
                )
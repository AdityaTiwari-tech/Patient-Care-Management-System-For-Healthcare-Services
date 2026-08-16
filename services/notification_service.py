"""Email notifications used across the app for login and report delivery."""
import html
import logging
import smtplib
from email.message import EmailMessage

from core.config import settings

logger = logging.getLogger(__name__)

# Brand colors, matching assets/styles.css — reused here so emails feel
# like they came from the same product rather than a bare system alert.
_INK = "#0E3B36"
_CLAY = "#E1614A"
_PARCHMENT = "#F7F5EF"
_SLATE = "#5B6864"


def send_email(
    to_email: str, subject: str, body: str,
    reply_to: str = None, from_name: str = None, html_body: str = None,
) -> bool:
    """Send an email using SMTP settings from the environment.

    The app is wired for Gmail SMTP. If the SMTP credentials are not set,
    the message is logged and silently skipped so the app keeps working in
    local/dev environments without failing the main workflow.

    from_name only changes the DISPLAY name on the From header (e.g.
    "Priya Sharma (Hospital Admin) <your-smtp-account@gmail.com>") — the
    actual sending address is always the authenticated SMTP account.
    Gmail's SPF/DKIM checks will reject or flag a From ADDRESS that isn't
    either that account or a verified "Send mail as" alias on it, so this
    is the one part of "from" you can safely customize without extra
    Gmail configuration.

    reply_to sets a Reply-To header — lets the recipient's reply go
    straight to a real person (e.g. the admin who triggered the email)
    even though the message itself was sent via the shared SMTP account.

    html_body, when given, sends a proper multipart/alternative email:
    `body` is still required as the plain-text fallback (some clients,
    and any preview pane, render that instead of the HTML part), while
    html_body is what most inboxes actually display. Use _email_shell()
    below to build a consistently-branded html_body rather than writing
    raw HTML at each call site.
    """
    if not to_email:
        return False

    if not settings.SMTP_HOST or not settings.SMTP_USERNAME or not settings.SMTP_PASSWORD:
        logger.info("SMTP credentials are not configured; skipping email to %s", to_email)
        return False

    from_address = settings.EMAIL_FROM or settings.SMTP_USERNAME
    display_from = f"{from_name} <{from_address}>" if from_name else from_address

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = display_from
    msg["To"] = to_email
    if reply_to:
        msg["Reply-To"] = reply_to
    msg.set_content(body)
    if html_body:
        msg.add_alternative(html_body, subtype="html")

    try:
        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT) as smtp:
            if settings.SMTP_USE_TLS:
                smtp.starttls()
            smtp.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
            smtp.send_message(msg)
        return True
    except Exception as exc:  # pragma: no cover - runtime SMTP failure path
        logger.exception("Failed to send email to %s: %s", to_email, exc)
        return False


def _email_shell(preheader: str, body_html: str, app_name: str = None) -> str:
    """Wraps `body_html` in a consistent letterhead: hospital name/logo
    line in the brand ink color, a clay accent rule, the content, and a
    footer — the same visual language as services/report_pdf.py's PDF
    template (same palette, same "this is a system-generated message"
    footer convention), so an email and a downloaded report feel like
    they came from the same product. Deliberately simple, table-free
    inline-CSS HTML (no flexbox/grid, no external stylesheet) since email
    clients — Gmail included — strip <style> blocks and support only a
    narrow, inconsistent slice of CSS; every rule here is inlined and
    kept to widely-supported properties for that reason.

    `preheader` is the short hidden snippet Gmail/Outlook show next to
    the subject line in the inbox list, before the email is opened —
    without one, clients fall back to showing the email's raw HTML,
    which looks broken (stray tags/whitespace) in the inbox preview.
    """
    app_name = app_name or settings.APP_NAME
    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background:{_PARCHMENT};font-family:Arial,Helvetica,sans-serif;">
    <div style="display:none;max-height:0;overflow:hidden;opacity:0;">{html.escape(preheader)}</div>
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{_PARCHMENT};padding:24px 0;">
        <tr><td align="center">
            <table role="presentation" width="560" cellpadding="0" cellspacing="0"
                   style="background:#FFFFFF;border-radius:10px;overflow:hidden;box-shadow:0 2px 10px rgba(14,59,54,0.08);">
                <tr>
                    <td style="background:{_INK};padding:20px 28px;">
                        <span style="color:#FFFFFF;font-size:19px;font-weight:bold;">🫀 {html.escape(app_name)}</span>
                        <div style="color:#CFE3DE;font-size:12px;margin-top:2px;">Cardiac Patient Care Portal</div>
                    </td>
                </tr>
                <tr><td style="height:3px;background:{_CLAY};font-size:0;line-height:0;">&nbsp;</td></tr>
                <tr>
                    <td style="padding:28px;color:#23302D;font-size:14px;line-height:1.6;">
                        {body_html}
                    </td>
                </tr>
                <tr>
                    <td style="padding:16px 28px;border-top:1px solid #E3E0D6;color:{_SLATE};font-size:11px;">
                        This is a system-generated message from {html.escape(app_name)}. For questions, contact your care team through the portal.
                    </td>
                </tr>
            </table>
        </td></tr>
    </table>
</body>
</html>"""


def _detail_row(label: str, value: str) -> str:
    """One label/value line inside an email's detail block — kept as a
    shared helper so every notification's detail list looks identical."""
    return (
        f'<tr><td style="padding:4px 12px 4px 0;color:{_SLATE};font-size:13px;white-space:nowrap;">{html.escape(label)}</td>'
        f'<td style="padding:4px 0;font-size:13px;font-weight:bold;color:{_INK};">{html.escape(value)}</td></tr>'
    )


def _sign_in_button(label: str = "Sign in now") -> str:
    """A centered clay-colored CTA button linking straight to the app
    (settings.APP_URL — see core/config.py). Shared so every email that
    should get someone into the app in one click looks identical. Table-
    based centering (not text-align on a div) because Outlook's rendering
    engine (Word-based) ignores several common centering approaches for
    inline-block elements."""
    return f"""
        <table role="presentation" cellpadding="0" cellspacing="0" width="100%" style="margin:22px 0 6px 0;">
            <tr><td align="center">
                <a href="{html.escape(settings.APP_URL)}"
                   style="display:inline-block;background:{_CLAY};color:#FFFFFF;text-decoration:none;
                          font-weight:bold;font-size:14px;padding:12px 32px;border-radius:6px;">
                    {html.escape(label)} &rarr;
                </a>
            </td></tr>
        </table>
        <p style="margin:6px 0 0 0;text-align:center;font-size:11px;color:{_SLATE};">
            Or go to: {html.escape(settings.APP_URL)}
        </p>
    """


def send_login_notification(user_email: str, full_name: str) -> bool:
    subject = f"{settings.APP_NAME} login successful"
    body = (
        f"Hello {full_name},\n\n"
        f"Your {settings.APP_NAME} login was successful."
        "\n\nIf this wasn't you, please contact support immediately."
    )
    return send_email(user_email, subject, body)


def send_doctor_account_created_email(
    doctor_email: str, doctor_name: str, password: str,
    admin_name: str = None, admin_email: str = None, app_name: str = None,
) -> bool:
    """Sent when an admin creates a new doctor login via
    services/auth_service.create_doctor_account(). Includes the password
    the admin set, since this is the doctor's first notice their account
    exists at all — they have no other way to know their login details.
    Formatted as a proper credentials banner: name, temporary password,
    full login block, and a direct one-click sign-in button (see
    _sign_in_button() — links to settings.APP_URL).

    admin_name/admin_email identify WHICH admin did this — pass the
    currently logged-in admin's own name/email (e.g. st.session_state.user
    in admin_portal.py) so the doctor can see who created their account
    and reply directly to them, rather than a generic "the admin"."""
    app_name = app_name or settings.APP_NAME
    from_name = f"{admin_name} ({app_name} Admin)" if admin_name else f"{app_name} Admin"
    creator = admin_name or "An administrator"

    subject = f"Your {app_name} account is ready, Dr. {doctor_name}"
    body = (
        f"Hello Dr. {doctor_name},\n\n"
        f"{creator} has created a doctor account for you on {app_name}.\n\n"
        "Your login credentials:\n"
        f"  Name: Dr. {doctor_name}\n"
        f"  Email: {doctor_email}\n"
        f"  Temporary password: {password}\n\n"
        f"Sign in here: {settings.APP_URL}\n\n"
        "For security, change this password (or ask your admin to reset "
        "it) after your first login.\n\n"
        + (f"Reply to this email to reach {admin_name} directly if you have "
           "any questions.\n\n" if admin_email else "")
        + "If you weren't expecting this account, please contact the admin."
    )

    reply_line = (
        f'<p style="margin:14px 0 0 0;font-size:13px;color:{_SLATE};">Reply to this email to reach '
        f'{html.escape(admin_name)} directly if you have any questions.</p>' if admin_email else ""
    )
    body_html = f"""
        <p style="margin:0 0 14px 0;">Hello Dr. {html.escape(doctor_name)},</p>
        <p style="margin:0 0 18px 0;">{html.escape(creator)} has created a doctor account for you on
            <strong>{html.escape(app_name)}</strong>. Your login credentials are below.</p>

        <table role="presentation" cellpadding="0" cellspacing="0"
               style="width:100%;background:{_PARCHMENT};border-left:3px solid {_CLAY};border-radius:4px;padding:16px 18px;margin:0 0 4px 0;">
            {_detail_row("Name", f"Dr. {doctor_name}")}
            {_detail_row("Email", doctor_email)}
            {_detail_row("Temporary password", password)}
        </table>
        <p style="margin:8px 0 0 0;font-size:12px;color:{_SLATE};">
            This password is temporary — change it (or ask your admin to reset it) right after your first login.</p>

        {_sign_in_button(f"Sign in to {app_name}")}

        {reply_line}
        <p style="margin:14px 0 0 0;color:{_SLATE};font-size:12px;">
            If you weren't expecting this account, please contact the admin.</p>
    """
    html_body = _email_shell(
        preheader=f"Your {app_name} doctor account is ready — credentials and sign-in link inside.",
        body_html=body_html, app_name=app_name,
    )

    return send_email(
        doctor_email, subject, body,
        reply_to=admin_email, from_name=from_name, html_body=html_body,
    )


def send_appointment_booked_email(
    doctor_email: str, doctor_name: str, patient_name: str,
    scheduled_date, start_time, reason: str = "", app_name: str = None,
) -> bool:
    """Sent to a doctor the moment a new appointment is booked with them —
    called from services/appointment_service.book_appointment() itself,
    so it fires regardless of which UI made the booking (the patient's
    own booking form in views/appointments_view.py, or the chatbot's
    booking wizard in ai/booking_flow.py).

    scheduled_date/start_time are the same date/time objects
    book_appointment() already has — formatted here rather than by the
    caller so every booking path gets identically worded emails."""
    app_name = app_name or settings.APP_NAME
    date_str = scheduled_date.strftime("%A, %d %B %Y")
    time_str = start_time.strftime("%I:%M %p")

    subject = f"New appointment: {patient_name} on {date_str}"
    body = (
        f"Hello Dr. {doctor_name},\n\n"
        f"{patient_name} has booked an appointment with you.\n\n"
        f"  Date: {date_str}\n"
        f"  Time: {time_str}\n"
        + (f"  Reason: {reason}\n" if reason else "")
        + f"\nSign in to {app_name} to view or manage this appointment: {settings.APP_URL}"
    )

    reason_row = _detail_row("Reason", reason) if reason else ""
    body_html = f"""
        <p style="margin:0 0 14px 0;">Hello Dr. {html.escape(doctor_name)},</p>
        <p style="margin:0 0 18px 0;"><strong>{html.escape(patient_name)}</strong> has booked an appointment with you.</p>
        <table role="presentation" cellpadding="0" cellspacing="0"
               style="width:100%;background:{_PARCHMENT};border-left:3px solid {_CLAY};border-radius:4px;padding:14px 16px;margin:0 0 6px 0;">
            {_detail_row("Patient", patient_name)}
            {_detail_row("Date", date_str)}
            {_detail_row("Time", time_str)}
            {reason_row}
        </table>
        {_sign_in_button("View in " + app_name)}
    """
    html_body = _email_shell(
        preheader=f"{patient_name} booked an appointment with you on {date_str}.",
        body_html=body_html, app_name=app_name,
    )

    return send_email(doctor_email, subject, body, html_body=html_body)
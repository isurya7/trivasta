# users/emails.py

import logging
from django.core.mail import EmailMultiAlternatives
from django.conf import settings

logger = logging.getLogger(__name__)

GOLD = '#c9a84c'
NAVY = '#0f3460'
SITE = 'https://trivasta.in'


def _support_email():
    """Returns support email from settings — never hardcoded."""
    return getattr(settings, 'SUPPORT_EMAIL', settings.DEFAULT_FROM_EMAIL)


def _base(body_html):
    support = _support_email()
    return f"""<!DOCTYPE html><html><head><meta charset="UTF-8">
<style>
body{{margin:0;padding:0;background:#f4f1eb;font-family:Arial,sans-serif}}
.w{{max-width:600px;margin:32px auto;background:#fff;border-radius:8px;overflow:hidden}}
.h{{background:{NAVY};padding:32px 40px;text-align:center}}
.logo{{font-size:22px;font-weight:700;color:{GOLD};letter-spacing:2px}}
.b{{padding:36px 40px}}
.btn{{display:inline-block;background:{GOLD};color:#fff;text-decoration:none;padding:12px 28px;border-radius:4px;font-weight:600;font-size:14px}}
.card{{background:#f9f7f2;border-radius:8px;padding:20px;margin:20px 0;border-left:4px solid {GOLD}}}
.row{{display:flex;justify-content:space-between;margin:0 0 8px;font-size:13px}}
.lbl{{color:#888}}.val{{font-weight:600;color:{NAVY}}}
.ft{{background:#f9f7f2;padding:20px 40px;text-align:center;font-size:12px;color:#aaa}}
.ft a{{color:{GOLD};text-decoration:none}}
h2{{font-size:20px;font-weight:600;color:{NAVY};margin:0 0 12px}}
p{{font-size:14px;color:#555;line-height:1.7;margin:0 0 16px}}
</style></head><body>
<div class="w">
  <div class="h"><div class="logo">TRIVASTA</div></div>
  <div class="b">{body_html}</div>
  <div class="ft">© 2025 Trivasta &nbsp;·&nbsp;
    <a href="{SITE}/privacy/">Privacy</a> &nbsp;·&nbsp;
    <a href="{SITE}/terms/">Terms</a> &nbsp;·&nbsp;
    <a href="mailto:{support}">{support}</a>
  </div>
</div></body></html>"""


def _send(subject, to_email, html, text):
    """
    Sends email via whatever backend is configured in settings.
    On Resend: set EMAIL_HOST=smtp.resend.com, EMAIL_HOST_PASSWORD=re_xxxxx
    On Brevo:  set EMAIL_HOST=smtp-relay.brevo.com, EMAIL_HOST_PASSWORD=brevo_smtp_key
    DEFAULT_FROM_EMAIL in settings controls the sender address.
    """
    try:
        msg = EmailMultiAlternatives(
            subject    = subject,
            body       = text,
            from_email = settings.DEFAULT_FROM_EMAIL,
            to         = [to_email],
        )
        msg.attach_alternative(html, 'text/html')
        msg.send()
        logger.info(f"Email sent: [{subject}] → {to_email}")
    except Exception as e:
        logger.error(f"Email failed [{subject}] → {to_email}: {e}")


# ── 1. Welcome ────────────────────────────────────────────────────────────────

def send_welcome_email(user):
    name    = user.first_name or user.username
    support = _support_email()
    html = _base(f"""
        <h2>Welcome, {name}!</h2>
        <p>Your Trivasta account is ready. Browse packages, connect with
        verified agencies, and plan your next adventure.</p>
        <p style="text-align:center;margin:28px 0">
          <a href="{SITE}/dashboard/" class="btn">Go to dashboard →</a>
        </p>
        <p style="font-size:13px;color:#888">Need help? Email us at
          <a href="mailto:{support}" style="color:{GOLD}">{support}</a>
        </p>
    """)
    text = (
        f"Welcome to Trivasta, {name}!\n\n"
        f"Your account is ready.\n"
        f"Dashboard: {SITE}/dashboard/\n\n"
        f"Need help? {support}"
    )
    _send(f'Welcome to Trivasta, {name}!', user.email, html, text)


# ── 2. Booking confirmation ───────────────────────────────────────────────────

def send_booking_confirmation(booking):
    user    = booking.user
    package = booking.package
    agency  = booking.agency
    name    = user.first_name or user.username

    pkg_rows = ''
    if package:
        pkg_rows = f"""
        <div class="row"><span class="lbl">Package</span>
          <span class="val">{package.title}</span></div>
        <div class="row"><span class="lbl">Destination</span>
          <span class="val">{package.destination}</span></div>
        <div class="row"><span class="lbl">Duration</span>
          <span class="val">{package.duration} days</span></div>"""

    agency_block = ''
    if agency:
        phone = f'<div style="font-size:13px;color:#555">📞 {agency.phone}</div>' if agency.phone else ''
        email = f'<div style="font-size:13px;color:#555">✉ {agency.email}</div>' if agency.email else ''
        agency_block = f"""
        <div class="card">
          <div style="font-size:11px;font-weight:600;letter-spacing:1px;
            text-transform:uppercase;color:{GOLD};margin-bottom:12px">Your Agency</div>
          <div style="font-size:15px;font-weight:600;color:{NAVY};
            margin-bottom:8px">{agency.name}</div>
          {phone}{email}
        </div>"""

    html = _base(f"""
        <h2>Booking #{booking.id} confirmed!</h2>
        <p>Payment received. Your agency will contact you within 24 hours.</p>
        <div class="card">
          <div style="font-size:11px;font-weight:600;letter-spacing:1px;
            text-transform:uppercase;color:{GOLD};margin-bottom:12px">Booking Details</div>
          <div class="row"><span class="lbl">Booking ID</span>
            <span class="val">#{booking.id}</span></div>
          {pkg_rows}
          <div class="row"><span class="lbl">Base amount</span>
            <span class="val">₹{booking.base_amount:,}</span></div>
          <div class="row"><span class="lbl">GST (5%)</span>
            <span class="val">₹{booking.gst_amount:,}</span></div>
          <div class="row" style="border-top:1px solid #e8e4da;
            padding-top:10px;margin-top:10px">
            <span class="lbl" style="font-weight:600;color:{NAVY}">Total paid</span>
            <span class="val" style="color:{GOLD};font-size:16px">
              ₹{booking.total_amount:,}</span>
          </div>
        </div>
        {agency_block}
        <p style="text-align:center;margin:28px 0">
          <a href="{SITE}/booking/{booking.id}/track/" class="btn">
            Track your trip →</a>
        </p>
    """)
    text = (
        f"Booking #{booking.id} Confirmed — Trivasta\n\n"
        f"Hi {name},\n\n"
        f"{'Package: ' + package.title + chr(10) if package else ''}"
        f"Total paid: ₹{booking.total_amount:,}\n\n"
        f"Track: {SITE}/booking/{booking.id}/track/"
    )
    _send(f'Booking #{booking.id} Confirmed — Trivasta', user.email, html, text)


# ── 3. Refund update ──────────────────────────────────────────────────────────

def send_refund_update(refund):
    user   = refund.requested_by
    name   = user.first_name or user.username
    is_ok  = refund.status == 'processed'

    badge_color = '#2e7d32' if is_ok else '#c62828'
    badge_bg    = '#e8f5e9' if is_ok else '#fdecea'
    badge_text  = 'Processed ✓' if is_ok else 'Rejected'

    note_html = (
        f"<p>Your refund of <strong>₹{refund.amount:,}</strong> has been processed. "
        f"Allow 5–7 business days for it to reach your account.</p>"
        if is_ok else
        f"<p>Your refund was not approved."
        f"{' Reason: ' + refund.rejection_reason if refund.rejection_reason else ''} "
        f"Contact support if you believe this is an error.</p>"
    )

    razorpay_row = ''
    if refund.razorpay_refund_id:
        razorpay_row = (
            f'<div class="row"><span class="lbl">Razorpay Ref</span>'
            f'<span class="val">{refund.razorpay_refund_id}</span></div>'
        )

    html = _base(f"""
        <h2>Refund update — Booking #{refund.booking.id}</h2>
        <p>Hi {name}, here's the latest on your refund request.</p>
        <div class="card">
          <div class="row"><span class="lbl">Refund ID</span>
            <span class="val">#{refund.id}</span></div>
          <div class="row"><span class="lbl">Booking</span>
            <span class="val">#{refund.booking.id}</span></div>
          <div class="row"><span class="lbl">Amount</span>
            <span class="val">₹{refund.amount:,}</span></div>
          <div class="row"><span class="lbl">Status</span>
            <span style="background:{badge_bg};color:{badge_color};
              padding:3px 10px;border-radius:20px;font-size:12px;
              font-weight:600">{badge_text}</span>
          </div>
          {razorpay_row}
        </div>
        {note_html}
        <p style="text-align:center;margin:28px 0">
          <a href="{SITE}/support/" class="btn">Contact support →</a>
        </p>
    """)
    text = (
        f"Refund Update — Booking #{refund.booking.id}\n\n"
        f"Amount: ₹{refund.amount:,}\n"
        f"Status: {refund.status.title()}\n"
        f"{'Razorpay Ref: ' + refund.razorpay_refund_id + chr(10) if refund.razorpay_refund_id else ''}\n"
        f"{'Refund processed. Allow 5-7 business days.' if is_ok else 'Refund not approved. ' + (refund.rejection_reason or '')}\n\n"
        f"Support: {SITE}/support/"
    )
    _send(
        f'Refund Update — Booking #{refund.booking.id} | Trivasta',
        user.email, html, text
    )


# ── 4. Support ticket acknowledgement ────────────────────────────────────────

def send_support_ack(ticket):
    user = ticket.user
    name = user.first_name or user.username

    html = _base(f"""
        <h2>We got your request, {name}.</h2>
        <p>Your ticket has been created. Here are the details:</p>
        <div class="card" style="text-align:center">
          <div style="font-size:11px;color:#888;letter-spacing:1px;
            text-transform:uppercase;margin-bottom:8px">Ticket ID</div>
          <div style="font-size:32px;font-weight:700;color:{GOLD}">#{ticket.id}</div>
          <div style="font-size:14px;color:{NAVY};margin-top:6px">{ticket.subject}</div>
        </div>
        <p>Our AI assistant is reviewing your case now. Complex issues are
        escalated to a human agent within <strong>2 hours</strong>.</p>
        <p style="text-align:center;margin:28px 0">
          <a href="{SITE}/support/" class="btn">View your ticket →</a>
        </p>
    """)
    text = (
        f"Support Ticket #{ticket.id} Received — Trivasta\n\n"
        f"Hi {name},\n\n"
        f"Subject: {ticket.subject}\n"
        f"Category: {ticket.get_category_display()}\n\n"
        f"We'll respond within 2 hours.\n"
        f"View: {SITE}/support/"
    )
    _send(
        f'We received your request — Ticket #{ticket.id} | Trivasta',
        user.email, html, text
    )


# ── 5. Trip status update ─────────────────────────────────────────────────────

def send_trip_status_update(booking, new_status, note=''):
    from marketplace.models import TripStatus
    user         = booking.user
    name         = user.first_name or user.username
    status_label = dict(TripStatus.STATUS_CHOICES).get(new_status, new_status)
    package      = booking.package
    agency       = booking.agency

    note_html = ''
    if note:
        note_html = (
            f'<div style="border-left:4px solid {GOLD};background:#fefcf5;'
            f'padding:14px 18px;border-radius:0 6px 6px 0;margin:16px 0">'
            f'<p style="font-style:italic;margin:0">"{note}"</p></div>'
        )

    pkg_rows = ''
    if package:
        pkg_rows = (
            f'<div class="row"><span class="lbl">Package</span>'
            f'<span class="val">{package.title}</span></div>'
            f'<div class="row"><span class="lbl">Destination</span>'
            f'<span class="val">{package.destination}</span></div>'
        )

    agency_name = f'<strong>{agency.name}</strong>' if agency else 'your agency'

    html = _base(f"""
        <h2>Your trip has been updated.</h2>
        <p>Hi {name}, {agency_name} updated your trip status.</p>
        <div style="background:linear-gradient(135deg,{NAVY},{NAVY}cc);
          border-radius:8px;padding:24px;text-align:center;margin:20px 0">
          <div style="color:rgba(255,255,255,0.6);font-size:11px;letter-spacing:2px;
            text-transform:uppercase;margin-bottom:8px">Current Status</div>
          <div style="color:{GOLD};font-size:20px;font-weight:700">{status_label}</div>
        </div>
        <div class="card">
          <div class="row"><span class="lbl">Booking ID</span>
            <span class="val">#{booking.id}</span></div>
          {pkg_rows}
        </div>
        {note_html}
        <p style="text-align:center;margin:28px 0">
          <a href="{SITE}/booking/{booking.id}/track/" class="btn">
            Track your trip →</a>
        </p>
    """)
    text = (
        f"Trip Update: {status_label} — Trivasta\n\n"
        f"Booking #{booking.id}\n"
        f"{'Package: ' + package.title + chr(10) if package else ''}"
        f"Status: {status_label}\n"
        f"{chr(10) + note if note else ''}\n\n"
        f"Track: {SITE}/booking/{booking.id}/track/"
    )
    _send(f'Trip Update: {status_label} — Trivasta', user.email, html, text)
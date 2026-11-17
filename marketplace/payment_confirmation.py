"""
One place that decides "has this booking really been paid?".

Every Razorpay callback (and later the webhook) goes through
confirm_booking_payment(), so the rules cannot drift apart between flows.
"""
import logging
from dataclasses import dataclass

from django.conf import settings
from django.db import IntegrityError, transaction

from .models import Booking, Coupon, CouponUsage, PayoutRecord
from .payment_service import (
    capture_razorpay_payment,
    fetch_razorpay_order,
    fetch_razorpay_payment,
    transfer_to_agency,
    verify_payment_signature,
)

logger = logging.getLogger(__name__)


class PaymentVerificationError(Exception):
    """The payment could not be confirmed. retryable=True means 'try again soon'."""

    def __init__(self, message, retryable=False):
        super().__init__(message)
        self.retryable = retryable


class InvalidSignatureError(PaymentVerificationError):
    """The callback was not signed by Razorpay."""


@dataclass
class ConfirmResult:
    booking: Booking
    newly_confirmed: bool          # False on a replayed callback
    duplicate_payment: bool = False  # a second, different payment hit a paid booking


def _to_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _order_notes(order):
    notes = order.get("notes")
    return notes if isinstance(notes, dict) else {}   # Razorpay sends [] when empty


def _capture_or_recheck(payment_id, payment):
    """
    The payment is 'authorized'. Capture it. If the capture call fails (for
    example because Razorpay's auto-capture got there first), look the payment
    up again and carry on if it is captured now.
    """
    try:
        capture_razorpay_payment(payment_id, _to_int(payment.get("amount")))
        return payment
    except Exception:
        logger.warning(
            "Capture call failed for payment %s; checking whether it is already captured",
            payment_id, exc_info=True,
        )

    try:
        latest = fetch_razorpay_payment(payment_id)
    except Exception as exc:
        raise PaymentVerificationError(
            "Could not confirm the payment status.", retryable=True
        ) from exc

    if latest.get("status") == "captured":
        return latest
    raise PaymentVerificationError("Payment authorised but not captured yet.", retryable=True)


def _fetch_gateway_data(order_id, payment_id):
    """Ask Razorpay what really happened. Never trust the browser's word."""
    try:
        order = fetch_razorpay_order(order_id)
        payment = fetch_razorpay_payment(payment_id)
    except Exception as exc:
        logger.exception("Razorpay lookup failed (order=%s payment=%s)", order_id, payment_id)
        raise PaymentVerificationError(
            "Could not reach Razorpay to verify the payment.", retryable=True
        ) from exc

    if payment.get("order_id") != order_id:
        raise PaymentVerificationError("Payment does not belong to this order.")
    if order.get("currency", "INR") != "INR":
        raise PaymentVerificationError("Unexpected currency.")

    status = payment.get("status")
    if status == "authorized":
        payment = _capture_or_recheck(payment_id, payment)
    elif status != "captured":
        raise PaymentVerificationError(f"Payment status is '{status}'.")

    if _to_int(payment.get("amount"), -1) != _to_int(order.get("amount"), -2):
        raise PaymentVerificationError("Payment amount does not match the order.")
    return order, payment


def _find_booking(order_id, notes):
    """Lock the booking row. Prefer the booking id stored in the order notes."""
    qs = Booking.objects.select_for_update()
    booking = None
    booking_id = _to_int(notes.get("booking_id"), 0)
    if booking_id:
        booking = qs.filter(pk=booking_id).first()
    if booking is None:
        booking = qs.filter(razorpay_order_id=order_id).first()
    return booking


def _record_payout_due(booking, discount_amount):
    """Create the 'pending' payout record. No money moves here."""
    agency = booking.agency
    if agency is None:
        logger.error("Booking %s has no agency — no payout record created", booking.pk)
        return None
    payout, _ = PayoutRecord.objects.get_or_create(
        booking=booking,
        defaults={
            "agency": agency,
            "total_amount": booking.total_amount,
            "base_amount": booking.base_amount,
            "gst_amount": booking.gst_amount,
            "discount_amount": discount_amount,
            "trivasta_commission": booking.commission_amount,
            "agency_payout_amount": booking.agency_payout,
            "status": "pending",
        },
    )
    return payout


def _record_coupon_usage(booking, coupon_code, discount_amount):
    """Coupon info comes from the order notes (not the browser session)."""
    if not coupon_code:
        return
    coupon = Coupon.objects.filter(code=coupon_code).first()
    if coupon is None or CouponUsage.objects.filter(booking=booking).exists():
        return
    try:
        with transaction.atomic():
            CouponUsage.objects.create(
                coupon=coupon, user=booking.user, booking=booking,
                discount_applied=discount_amount,
            )
        coupon.mark_used()
    except IntegrityError:
        logger.warning("Coupon %s already used by user %s", coupon_code, booking.user_id)


def _try_auto_payout(booking_id):
    try:
        booking = Booking.objects.get(pk=booking_id)
        amounts = {
            "total_amount": booking.total_amount,
            "base_amount": booking.base_amount,
            "gst_amount": booking.gst_amount,
            "trivasta_commission": booking.commission_amount,
            "agency_payout": booking.agency_payout,
            "discount_amount": 0,
        }
        _, error = transfer_to_agency(booking, amounts)
        if error:
            logger.warning("Payout for booking %s not sent: %s", booking_id, error)
    except Exception:
        logger.exception("Auto payout crashed for booking %s", booking_id)


def confirm_booking_payment(order_id, payment_id, signature,
                            expected_booking_id=None, expected_package_id=None):
    """
    Confirm a payment reported by the customer's browser.
    Checks Razorpay's signature first, then everything in
    confirm_verified_payment().
    """
    if not verify_payment_signature(order_id, payment_id, signature):
        logger.warning("Invalid payment signature (order=%s)", order_id)
        raise InvalidSignatureError("Invalid payment signature.")
    return confirm_verified_payment(
        order_id, payment_id,
        expected_booking_id=expected_booking_id,
        expected_package_id=expected_package_id,
    )


def confirm_verified_payment(order_id, payment_id,
                             expected_booking_id=None, expected_package_id=None):
    """
    Confirm a payment by asking Razorpay directly (no browser signature needed).
    Used by confirm_booking_payment() and for confirming a stuck payment by hand.

    Safe to call more than once for the same payment: the booking is locked,
    and a replay returns newly_confirmed=False without changing anything.
    Raises PaymentVerificationError if anything does not add up.
    """
    order, payment = _fetch_gateway_data(order_id, payment_id)
    notes = _order_notes(order)
    paid_paise = _to_int(order.get("amount"))
    payout = None

    with transaction.atomic():
        booking = _find_booking(order_id, notes)
        if booking is None:
            logger.error("PAID BUT NO BOOKING: order=%s payment=%s", order_id, payment_id)
            raise PaymentVerificationError("No booking found for this payment.")

        if expected_booking_id is not None and booking.pk != expected_booking_id:
            logger.error("Payment %s belongs to booking %s, not %s",
                         payment_id, booking.pk, expected_booking_id)
            raise PaymentVerificationError("Payment does not belong to this booking.")

        if expected_package_id is not None and booking.package_id != expected_package_id:
            logger.error("Payment %s is for booking %s, which is not package %s",
                         payment_id, booking.pk, expected_package_id)
            raise PaymentVerificationError("Payment does not belong to this package.")

        if paid_paise != booking.total_amount * 100:
            logger.error("AMOUNT MISMATCH: booking=%s expected=%s paid=%s payment=%s",
                         booking.pk, booking.total_amount * 100, paid_paise, payment_id)
            raise PaymentVerificationError("Paid amount does not match the booking.")

        if booking.is_paid:
            duplicate = bool(booking.razorpay_payment_id) and booking.razorpay_payment_id != payment_id
            if duplicate:
                logger.error("DUPLICATE PAYMENT: booking=%s first=%s second=%s — refund needed",
                             booking.pk, booking.razorpay_payment_id, payment_id)
            return ConfirmResult(booking, False, duplicate)

        booking.is_paid = True
        booking.status = "confirmed"
        booking.razorpay_payment_id = payment_id
        booking.razorpay_order_id = order_id
        booking.save(update_fields=["is_paid", "status", "razorpay_payment_id", "razorpay_order_id"])

        discount = _to_int(notes.get("discount_amount"))
        payout = _record_payout_due(booking, discount)
        _record_coupon_usage(booking, notes.get("coupon"), discount)

    if payout is not None and getattr(settings, "AUTO_PAYOUTS_ENABLED", False):
        transaction.on_commit(lambda: _try_auto_payout(booking.pk))

    return ConfirmResult(booking, True, False)
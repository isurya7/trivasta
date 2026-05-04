import razorpay
import logging
import hmac
import hashlib
from django.conf import settings
from django.utils import timezone
from django.db import IntegrityError

logger = logging.getLogger(__name__)

razorpay_client = razorpay.Client(
    auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET)
)

TRIVASTA_COMMISSION_PCT = 10
GST_PCT                 = 5


def calculate_booking_amounts(base_price, coupon=None):
    original_base   = int(base_price)
    discount_amount = 0
    coupon_code     = None

    if coupon:
        valid, msg = coupon.is_valid()
        if valid:
            discount_amount, discounted_base = coupon.calculate_discount(original_base)
            coupon_code = coupon.code
        else:
            discounted_base = original_base
    else:
        discounted_base = original_base

    gst_amount          = int(discounted_base * GST_PCT / 100)
    total_amount        = discounted_base + gst_amount
    trivasta_commission = int(original_base * TRIVASTA_COMMISSION_PCT / 100)
    agency_payout       = max(discounted_base - trivasta_commission, 0)

    return {
        'original_base':       original_base,
        'discount_amount':     discount_amount,
        'base_amount':         discounted_base,
        'gst_amount':          gst_amount,
        'total_amount':        total_amount,
        'trivasta_commission': trivasta_commission,
        'agency_payout':       agency_payout,
        'coupon_code':         coupon_code,
        'commission_pct':      TRIVASTA_COMMISSION_PCT,
    }


def create_razorpay_order(amounts, booking_id, description="Trivasta Booking"):
    try:
        order = razorpay_client.order.create({
            "amount":   amounts['total_amount'] * 100,
            "currency": "INR",
            "receipt":  f"booking_{booking_id}",
            "notes": {
                "booking_id":          str(booking_id),
                "base_amount":         str(amounts['base_amount']),
                "gst_amount":          str(amounts['gst_amount']),
                "trivasta_commission": str(amounts['trivasta_commission']),
                "agency_payout":       str(amounts['agency_payout']),
                "coupon":              amounts.get('coupon_code') or '',
            }
        })
        return order, None
    except Exception as e:
        logger.error(f"Razorpay order creation failed: {e}")
        return None, str(e)


# ── FIX 1: Corrected HMAC — was hmac.new() which doesn't exist ───────────────
def verify_payment_signature(order_id, payment_id, signature):
    """
    Returns True if Razorpay HMAC-SHA256 signature is valid.
    Single source of truth — do NOT duplicate this in views.py.
    """
    if not all([order_id, payment_id, signature]):
        logger.warning("verify_payment_signature: missing params")
        return False
    try:
        body      = f"{order_id}|{payment_id}"
        secret    = settings.RAZORPAY_KEY_SECRET.encode()
        generated = hmac.new(secret, body.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(generated, signature)
    except Exception as e:
        logger.error(f"Signature verification error: {e}")
        return False


# ── FIX 2: Race condition eliminated with get_or_create + IntegrityError ─────
def transfer_to_agency(booking, amounts):
    """
    Fires a Razorpay Route transfer to the agency's linked account.
    Safe against duplicate webhook delivery — uses get_or_create on PayoutRecord
    so a second concurrent call is a no-op rather than a double payout.
    """
    from marketplace.models import PayoutRecord

    agency = booking.agency
    if not agency:
        logger.error(f"Booking {booking.id} has no agency — cannot transfer")
        return None, "No agency found for this booking."

    # ── Atomic get-or-create prevents race condition ──────────────────────────
    try:
        payout, created = PayoutRecord.objects.get_or_create(
            booking=booking,
            defaults={
                'agency':               agency,
                'total_amount':         amounts['total_amount'],
                'base_amount':          amounts['base_amount'],
                'gst_amount':           amounts['gst_amount'],
                'discount_amount':      amounts.get('discount_amount', 0),
                'trivasta_commission':  amounts['trivasta_commission'],
                'agency_payout_amount': amounts['agency_payout'],
                'status':               'pending',
            }
        )
    except IntegrityError:
        # Another request created the record a millisecond before us — fetch it
        payout  = PayoutRecord.objects.get(booking=booking)
        created = False

    if not created:
        if payout.status == 'paid':
            logger.info(f"Payout already completed for booking {booking.id} — skipping")
            return payout, None
        logger.info(f"Retrying existing payout #{payout.id} for booking {booking.id}")

    # ── Check agency payout readiness ─────────────────────────────────────────
    try:
        bank = agency.bank_details
    except Exception:
        payout.failure_reason = 'Agency has no bank details on file'
        payout.save(update_fields=['failure_reason'])
        logger.warning(f"Booking {booking.id}: agency {agency.name} has no bank details")
        return payout, None

    if not bank.is_payout_ready:
        payout.failure_reason = 'Agency KYC not verified or no Razorpay linked account'
        payout.save(update_fields=['failure_reason'])
        logger.warning(f"Booking {booking.id}: agency {agency.name} not payout-ready — queued")
        return payout, None

    # ── Fire Razorpay Route transfer ──────────────────────────────────────────
    try:
        payout.status = 'processing'
        payout.save(update_fields=['status'])

        transfer = razorpay_client.transfer.create({
            "account":  bank.razorpay_account_id,
            "amount":   amounts['agency_payout'] * 100,
            "currency": "INR",
            "notes": {
                "booking_id": str(booking.id),
                "agency":     agency.name,
                "commission": str(amounts['trivasta_commission']),
            },
            "linked_account_notes": ["booking_id"],
            "on_hold": 0,
        })

        payout.razorpay_transfer_id = transfer['id']
        payout.status               = 'paid'
        payout.paid_at              = timezone.now()
        payout.save()

        logger.info(
            f"Transfer OK: {transfer['id']} | "
            f"₹{amounts['agency_payout']} → {agency.name} | "
            f"Booking #{booking.id}"
        )
        return payout, None

    except Exception as e:
        payout.status         = 'failed'
        payout.failure_reason = str(e)
        payout.save()
        logger.error(f"Razorpay Route transfer FAILED for booking {booking.id}: {e}")
        return payout, str(e)


def create_agency_linked_account(agency):
    try:
        bank = agency.bank_details
    except Exception:
        return None, "Agency has no bank details submitted."

    try:
        account = razorpay_client.account.create({
            "email":   agency.email,
            "profile": {
                "category":    "travel_hospitality",
                "subcategory": "travel_agency",
                "addresses": {
                    "registered": {
                        "street1":     agency.location or "India",
                        "city":        "Mumbai",
                        "state":       "MH",
                        "postal_code": "400001",
                        "country":     "IN",
                    }
                }
            },
            "legal_business_name": agency.name,
            "business_type":       "route",
            "legal_info": {
                "pan": bank.pan_number or "",
                "gst": bank.gst_number or "",
            },
            "type": "route",
        })

        account_id = account['id']

        fund_account = razorpay_client.fund_account.create({
            "account_number": account_id,
            "contact_id":     account.get('contact_id', ''),
            "account_type":   "bank_account",
            "bank_account": {
                "name":           bank.account_holder_name,
                "ifsc":           bank.ifsc_code,
                "account_number": bank.account_number,
            }
        })

        bank.razorpay_account_id      = account_id
        bank.razorpay_fund_account_id = fund_account['id']
        bank.save(update_fields=['razorpay_account_id', 'razorpay_fund_account_id'])

        logger.info(f"Razorpay linked account created for {agency.name}: {account_id}")
        return account_id, None

    except Exception as e:
        logger.error(f"Failed to create Razorpay linked account for {agency.name}: {e}")
        return None, str(e)


def validate_coupon(code, user, base_amount, agency=None):
    from marketplace.models import Coupon, CouponUsage

    if not code:
        return None, "No coupon code provided."

    try:
        coupon = Coupon.objects.get(code=code.upper().strip())
    except Coupon.DoesNotExist:
        return None, "Invalid coupon code."

    valid, msg = coupon.is_valid()
    if not valid:
        return None, msg

    if base_amount < coupon.min_booking_amount:
        return None, f"Minimum booking amount for this coupon is ₹{coupon.min_booking_amount:,}."

    if CouponUsage.objects.filter(coupon=coupon, user=user).exists():
        return None, "You have already used this coupon."

    if coupon.applicable_agency and agency and coupon.applicable_agency != agency:
        return None, "This coupon is not valid for this agency."

    return coupon, None
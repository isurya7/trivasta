import razorpay
import logging
from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)

razorpay_client = razorpay.Client(
    auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET)
)

# ── Commission config ─────────────────────────────────────────────────────────
TRIVASTA_COMMISSION_PCT = 10   # 10% commission (fixed from 5%)
GST_PCT                 = 5    # 5% GST on base amount


# ── 1. Price calculation ──────────────────────────────────────────────────────

def calculate_booking_amounts(base_price, coupon=None):
    """
    Given a base package/offer price and optional coupon,
    returns a dict with all amounts broken down.

    Trivasta always takes 10% of ORIGINAL base price.
    Coupon discount reduces agency payout — Trivasta never loses commission.
    """
    original_base    = int(base_price)
    discount_amount  = 0
    coupon_code      = None

    if coupon:
        valid, msg = coupon.is_valid()
        if valid:
            discount_amount, discounted_base = coupon.calculate_discount(original_base)
            coupon_code = coupon.code
        else:
            discounted_base = original_base
    else:
        discounted_base = original_base

    gst_amount           = int(discounted_base * GST_PCT / 100)
    total_amount         = discounted_base + gst_amount
    trivasta_commission  = int(original_base * TRIVASTA_COMMISSION_PCT / 100)  # always on original
    agency_payout        = discounted_base - trivasta_commission

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


# ── 2. Create Razorpay order ──────────────────────────────────────────────────

def create_razorpay_order(amounts, booking_id, description="Trivasta Booking"):
    """Creates a Razorpay order for the total amount."""
    try:
        order = razorpay_client.order.create({
            "amount":   amounts['total_amount'] * 100,  # paise
            "currency": "INR",
            "receipt":  f"booking_{booking_id}",
            "notes": {
                "booking_id":          booking_id,
                "base_amount":         amounts['base_amount'],
                "gst_amount":          amounts['gst_amount'],
                "trivasta_commission": amounts['trivasta_commission'],
                "agency_payout":       amounts['agency_payout'],
                "coupon":              amounts.get('coupon_code') or '',
            }
        })
        return order, None
    except Exception as e:
        logger.error(f"Razorpay order creation failed: {e}")
        return None, str(e)


# ── 3. Verify payment signature ───────────────────────────────────────────────

def verify_payment_signature(order_id, payment_id, signature):
    """Returns True if Razorpay signature is valid."""
    import hmac
    import hashlib
    generated = hmac.new(
        settings.RAZORPAY_KEY_SECRET.encode(),
        f"{order_id}|{payment_id}".encode(),
        hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(generated, signature)


# ── 4. Transfer to agency (Razorpay Route) ───────────────────────────────────

def transfer_to_agency(booking, amounts):
    """
    Fires Razorpay Route transfer to agency's linked account.
    Called immediately after payment is verified.

    Returns (payout_record, error_message)
    """
    from marketplace.models import PayoutRecord

    agency = booking.agency
    if not agency:
        return None, "No agency found for this booking."

    # Create payout record first
    payout = PayoutRecord.objects.create(
        booking              = booking,
        agency               = agency,
        total_amount         = amounts['total_amount'],
        base_amount          = amounts['base_amount'],
        gst_amount           = amounts['gst_amount'],
        discount_amount      = amounts.get('discount_amount', 0),
        trivasta_commission  = amounts['trivasta_commission'],
        agency_payout_amount = amounts['agency_payout'],
        status               = 'pending',
    )

    # Check if agency has verified bank details
    try:
        bank = agency.bank_details
        if not bank.is_payout_ready:
            payout.failure_reason = 'Agency KYC not verified or no linked account'
            payout.save(update_fields=['failure_reason'])
            logger.warning(f"Agency {agency.name} not payout-ready — transfer queued")
            return payout, None  # Not an error — will be processed manually
    except Exception:
        payout.failure_reason = 'Agency has no bank details on file'
        payout.save(update_fields=['failure_reason'])
        return payout, None

    # Fire Razorpay Route transfer
    try:
        payout.status = 'processing'
        payout.save(update_fields=['status'])

        transfer = razorpay_client.transfer.create({
            "account":   bank.razorpay_account_id,
            "amount":    amounts['agency_payout'] * 100,  # paise
            "currency":  "INR",
            "notes": {
                "booking_id": booking.id,
                "agency":     agency.name,
                "commission": amounts['trivasta_commission'],
            },
            "linked_account_notes": ["booking_id"],
            "on_hold":   0,  # 0 = instant transfer
        })

        payout.razorpay_transfer_id = transfer['id']
        payout.status               = 'paid'
        payout.paid_at              = timezone.now()
        payout.save()

        logger.info(f"Transfer successful: {transfer['id']} ₹{amounts['agency_payout']} to {agency.name}")
        return payout, None

    except Exception as e:
        payout.status         = 'failed'
        payout.failure_reason = str(e)
        payout.save()
        logger.error(f"Razorpay Route transfer failed for booking {booking.id}: {e}")
        return payout, str(e)


# ── 5. Create Razorpay linked account for agency ──────────────────────────────

def create_agency_linked_account(agency):
    """
    Creates a Razorpay Route linked account for the agency.
    Called when admin verifies agency KYC.
    Returns (account_id, error)
    """
    try:
        bank = agency.bank_details
    except Exception:
        return None, "Agency has no bank details."

    try:
        account = razorpay_client.account.create({
            "email":   agency.email,
            "profile": {
                "category":    "travel_hospitality",
                "subcategory": "travel_agency",
                "addresses": {
                    "registered": {
                        "street1":     agency.location or "India",
                        "city":        "India",
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

        return account_id, None

    except Exception as e:
        logger.error(f"Failed to create Razorpay linked account for {agency.name}: {e}")
        return None, str(e)


# ── 6. Validate coupon ────────────────────────────────────────────────────────

def validate_coupon(code, user, base_amount, agency=None):
    """
    Validates a coupon code and returns (coupon_obj, error_message).
    Checks: exists, active, not expired, min amount, not already used by user.
    """
    from marketplace.models import Coupon, CouponUsage

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
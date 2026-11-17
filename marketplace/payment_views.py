import logging

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.csrf import csrf_exempt

from .models import Booking, ChatRoom, Message, Offer, Package, PaymentRequest
from .payment_confirmation import (
    InvalidSignatureError,
    PaymentVerificationError,
    confirm_booking_payment,
)
from .payment_service import GST_PCT, calculate_booking_amounts, create_razorpay_order

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _pricing_dict(amounts):
    return {
        'base_amount':       amounts['base_amount'],
        'gst_amount':        amounts['gst_amount'],
        'total_amount':      amounts['total_amount'],
        'commission_amount': amounts['trivasta_commission'],
        'agency_payout':     amounts['agency_payout'],
    }


def _booking_fields(amounts):
    return {
        'base_amount':       amounts['base_amount'],
        'gst_amount':        amounts['gst_amount'],
        'commission_amount': amounts['trivasta_commission'],
        'agency_payout':     amounts['agency_payout'],
        'total_amount':      amounts['total_amount'],
    }


def _find_reusable_booking(user, offer=None, package=None):
    """
    The latest unpaid booking for this user and item, so refreshing the page or
    opening a second tab does not create a pile of bookings.
    Bookings that belong to a chat payment request are skipped.
    """
    candidates = Booking.objects.filter(
        user=user, is_paid=False, offer=offer, package=package
    ).order_by('-id')[:10]
    for booking in candidates:
        if booking.razorpay_order_id and PaymentRequest.objects.filter(
                razorpay_order_id=booking.razorpay_order_id).exists():
            continue
        return booking
    return None


def _prepare_booking(user, amounts, offer=None, package=None):
    fields = _booking_fields(amounts)
    booking = _find_reusable_booking(user, offer=offer, package=package)
    if booking is None:
        return Booking.objects.create(
            user=user, offer=offer, package=package,
            status='pending', is_paid=False, **fields
        )
    if any(getattr(booking, key) != value for key, value in fields.items()):
        for key, value in fields.items():
            setattr(booking, key, value)
        booking.razorpay_order_id = None      # price changed: a new order is needed
        booking.save()
    return booking


def _ensure_order(booking, amounts, description):
    """Reuse the booking's Razorpay order if it has one; never overwrite it."""
    if booking.razorpay_order_id:
        return booking.razorpay_order_id, None
    order, error = create_razorpay_order(amounts, booking.id, description)
    if error:
        return None, error
    booking.razorpay_order_id = order['id']
    booking.save(update_fields=['razorpay_order_id'])
    return order['id'], None


def _callback_params(request):
    return (
        request.POST.get('razorpay_order_id'),
        request.POST.get('razorpay_payment_id'),
        request.POST.get('razorpay_signature'),
    )


def _verification_failed(request, exc, payment_id):
    """Turn a verification error into a calm message for the customer."""
    logger.error("Payment verification failed: %s (payment=%s)", exc, payment_id)
    if isinstance(exc, InvalidSignatureError):
        return redirect('payment_failed')
    if exc.retryable:
        messages.warning(
            request,
            "We're still confirming your payment with the bank. Please don't pay "
            "again. If your booking isn't in your dashboard within 15 minutes, "
            f"contact support and quote this reference: {payment_id}.",
        )
        return redirect('dashboard')
    messages.error(
        request,
        "We couldn't confirm your payment automatically. If money was deducted, "
        f"please contact support with this reference: {payment_id}.",
    )
    return redirect('payment_failed')


def _contact_unlock_text(agency, headline):
    text = (
        f"✅ {headline}\n\n"
        f"🔓 Agency contact details are now unlocked:\n"
        f"📞 {agency.phone}\n📧 {agency.email}"
    )
    if agency.website:
        text += f"\n🌐 {agency.website}"
    return text + "\n\nOur team will reach out within 24 hours."


def _unlock_package_chat(booking):
    """Create the package chat room and post the contact details (paid bookings only)."""
    package = booking.package
    if package is None:
        return
    agency = package.agency
    room, _ = ChatRoom.objects.get_or_create(
        package=package, user=booking.user, agency=agency, defaults={'offer': None}
    )
    Message.objects.create(
        room=room, sender_type='system',
        content=_contact_unlock_text(
            agency, f"Payment of ₹{booking.total_amount:,} confirmed for **{package.title}**!"
        ),
    )


def _finish_package_payment(request, result):
    booking = result.booking
    if result.duplicate_payment:
        messages.warning(
            request,
            "We received a second payment for this booking. Our team will refund it.",
        )
    if result.newly_confirmed:
        try:
            _unlock_package_chat(booking)
        except Exception:
            logger.exception("Could not unlock chat for booking %s", booking.pk)
        try:
            from users.emails import send_booking_confirmation
            send_booking_confirmation(booking)
        except Exception:
            logger.exception("Booking confirmation email failed for booking %s", booking.pk)
    return redirect('package_booking_confirmation', booking_id=booking.id)


# ─────────────────────────────────────────────────────────────────────────────
# Package payments
# ─────────────────────────────────────────────────────────────────────────────

@login_required
def package_book(request, pk):
    package = get_object_or_404(Package, pk=pk, is_active=True)
    amounts = calculate_booking_amounts(package.price)

    booking = _prepare_booking(request.user, amounts, package=package)
    order_id, error = _ensure_order(booking, amounts, f"Package: {package.title}")
    if error:
        messages.error(request, "We couldn't start the payment. Please try again.")
        return redirect('package_detail', pk=pk)

    return render(request, 'marketplace/package_checkout.html', {
        'package':           package,
        'pricing':           _pricing_dict(amounts),
        'razorpay_key':      settings.RAZORPAY_KEY_ID,
        'razorpay_order_id': order_id,
        'user':              request.user,
    })


@csrf_exempt
def package_book_success(request, pk):
    if request.method != 'POST':
        return redirect('dashboard')

    order_id, payment_id, signature = _callback_params(request)
    try:
        result = confirm_booking_payment(
            order_id, payment_id, signature, expected_package_id=pk
        )
    except PaymentVerificationError as exc:
        return _verification_failed(request, exc, payment_id)
    return _finish_package_payment(request, result)


@csrf_exempt
def package_payment_success(request, booking_id):
    """Callback for the newer book_package flow (coupons)."""
    if request.method != 'POST':
        return redirect('dashboard')

    order_id, payment_id, signature = _callback_params(request)
    try:
        result = confirm_booking_payment(
            order_id, payment_id, signature, expected_booking_id=booking_id
        )
    except PaymentVerificationError as exc:
        return _verification_failed(request, exc, payment_id)
    return _finish_package_payment(request, result)


# ─────────────────────────────────────────────────────────────────────────────
# Chat payment requests
# ─────────────────────────────────────────────────────────────────────────────

@login_required
def accept_payment_request(request, pr_id):
    pr   = get_object_or_404(PaymentRequest, pk=pr_id, room__user=request.user, status='pending')
    room = pr.room
    amounts = calculate_booking_amounts(pr.amount)

    booking = None
    if pr.razorpay_order_id:
        booking = Booking.objects.filter(
            razorpay_order_id=pr.razorpay_order_id, user=request.user, is_paid=False
        ).first()

    if booking is None:
        if room.offer_id:
            booking = Booking.objects.create(
                user=request.user, offer=room.offer, status='pending',
                **_booking_fields(amounts)
            )
        elif room.package_id:
            booking = Booking.objects.create(
                user=request.user, package=room.package, status='pending',
                **_booking_fields(amounts)
            )
        else:
            messages.error(request, "This chat isn't linked to an offer or package.")
            return redirect('chat_room', room_id=room.id)

        order_id, error = _ensure_order(booking, amounts, "Negotiated trip payment")
        if error:
            booking.delete()
            messages.error(request, "We couldn't start the payment. Please try again.")
            return redirect('chat_room', room_id=room.id)
        pr.razorpay_order_id = order_id
        pr.save(update_fields=['razorpay_order_id'])
    else:
        order_id = booking.razorpay_order_id

    return render(request, 'marketplace/chat_checkout.html', {
        'pr':                pr,
        'room':              room,
        'pricing':           _pricing_dict(amounts),
        'base_amount':       amounts['base_amount'],
        'gst_amount':        amounts['gst_amount'],
        'total':             amounts['total_amount'],
        'gst_pct':           GST_PCT,
        'razorpay_key':      settings.RAZORPAY_KEY_ID,
        'razorpay_order_id': order_id,
        'user':              request.user,
    })


@csrf_exempt
def chat_payment_success(request):
    if request.method != 'POST':
        return redirect('dashboard')

    order_id, payment_id, signature = _callback_params(request)
    try:
        result = confirm_booking_payment(order_id, payment_id, signature)
    except PaymentVerificationError as exc:
        return _verification_failed(request, exc, payment_id)

    pr = PaymentRequest.objects.filter(razorpay_order_id=order_id).select_related('room').first()
    if pr is None:
        logger.error("Booking %s paid but no PaymentRequest for order %s",
                     result.booking.pk, order_id)
        return redirect('dashboard')

    if pr.status != 'paid':
        pr.status = 'paid'
        pr.razorpay_payment_id = payment_id
        pr.save(update_fields=['status', 'razorpay_payment_id'])

    if result.duplicate_payment:
        messages.warning(
            request, "We received a second payment for this booking. Our team will refund it."
        )

    if result.newly_confirmed:
        try:
            Message.objects.create(
                room=pr.room, sender_type='system',
                content=_contact_unlock_text(
                    pr.room.agency, f"Payment of ₹{result.booking.total_amount:,} confirmed!"
                ),
            )
        except Exception:
            logger.exception("Could not post contact details for booking %s", result.booking.pk)

    return redirect('chat_room', room_id=pr.room_id)


# ─────────────────────────────────────────────────────────────────────────────
# Offer checkout (direct payment of an agency offer)
# ─────────────────────────────────────────────────────────────────────────────

@login_required
def checkout(request, offer_id):
    offer = get_object_or_404(Offer, pk=offer_id, trip__user=request.user)
    if Booking.objects.filter(offer=offer, is_paid=True).exists():
        messages.info(request, "This offer has already been booked.")
        return redirect('dashboard')

    amounts = calculate_booking_amounts(offer.price)
    booking = _prepare_booking(request.user, amounts, offer=offer)
    order_id, error = _ensure_order(booking, amounts, f"Trip to {offer.trip.destination}")
    if error:
        messages.error(request, "We couldn't start the payment. Please try again.")
        return redirect('offers', trip_id=offer.trip_id)

    return render(request, 'marketplace/checkout.html', {
        'offer':             offer,
        'pricing':           _pricing_dict(amounts),
        'base_amount':       amounts['base_amount'],
        'gst_amount':        amounts['gst_amount'],
        'total':             amounts['total_amount'],
        'gst_pct':           GST_PCT,
        'razorpay_key':      settings.RAZORPAY_KEY_ID,
        'razorpay_order_id': order_id,
        'user':              request.user,
    })


@csrf_exempt
def offer_payment_success(request):
    if request.method != 'POST':
        return redirect('dashboard')

    order_id, payment_id, signature = _callback_params(request)
    try:
        result = confirm_booking_payment(order_id, payment_id, signature)
    except PaymentVerificationError as exc:
        return _verification_failed(request, exc, payment_id)

    if result.duplicate_payment:
        messages.warning(
            request, "We received a second payment for this booking. Our team will refund it."
        )
    return redirect('booking_confirmation', booking_id=result.booking.id)
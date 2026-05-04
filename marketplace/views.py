import razorpay
import json
import logging
from django.conf import settings
from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.models import User
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from datetime import timedelta
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.db.models import Q, Avg

from .ai_support import get_ai_support_response
from .payment_service import (
    calculate_booking_amounts,
    create_razorpay_order,
    verify_payment_signature,   
    transfer_to_agency,
    validate_coupon,
    create_agency_linked_account,
)
from trips.models import Trip
from .contact_guard import is_violation, classify_violation
from .models import (
    Offer, Agency, Booking, Package, Message,
    PaymentRequest, ChatRoom, AgencyWarning,
    TripStatus, TripUpdate, PackageView, PackageReview,
    SupportTicket, SupportMessage, RefundRequest, AgencyBankDetails,
    PayoutRecord, Coupon, CouponUsage,
)
from .forms import (
    AgencyRegisterForm, PackageForm, OfferForm, AgencyProfileForm,
    PackageImageForm, PackageReviewForm, PackageImageFormSet,
)

logger = logging.getLogger(__name__)

# One Razorpay client — used for order creation only.
# Payouts go through payment_service.razorpay_client.

COMMISSION_RATE = 0.10
GST_RATE        = 0.05

PLAN_PRICES = {
    'starter':      4999,
    'professional': 9999,
    'enterprise':   19999,
}


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def get_agency(request):
    try:
        return Agency.objects.get(user=request.user)
    except Agency.DoesNotExist:
        return None


def compute_pricing(base_amount):
    """
    Legacy helper used in a few older views (package_book, accept_payment_request).
    New flows should use calculate_booking_amounts() from payment_service instead.
    """
    gst        = int(base_amount * GST_RATE)
    total      = base_amount + gst
    commission = int(base_amount * COMMISSION_RATE)
    payout     = base_amount - commission
    return {
        'base_amount':       base_amount,
        'gst_amount':        gst,
        'total_amount':      total,
        'commission_amount': commission,
        'agency_payout':     payout,
    }


def _amounts_from_pricing(pricing):
    """
    Converts compute_pricing() output → the shape expected by transfer_to_agency().
    Keeps legacy views compatible with the unified payout logic.
    """
    return {
        'total_amount':        pricing['total_amount'],
        'base_amount':         pricing['base_amount'],
        'gst_amount':          pricing['gst_amount'],
        'trivasta_commission': pricing['commission_amount'],
        'agency_payout':       pricing['agency_payout'],
        'discount_amount':     0,
    }


def agency_required(view_func):
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('agency_login')
        agency = get_agency(request)
        if not agency:
            return redirect('agency_login')
        if agency.status == 'rejected':
            messages.error(request, "Your application was rejected.")
            return redirect('agency_login')
        if agency.status == 'pending':
            return render(request, 'marketplace/agency_pending.html', {'agency': agency})
        return view_func(request, *args, **kwargs)
    wrapper.__name__ = view_func.__name__
    return wrapper


# ─────────────────────────────────────────────────────────────────────────────
# AI Support Chat
# ─────────────────────────────────────────────────────────────────────────────

@login_required
def support_chat(request):
    ticket = SupportTicket.objects.filter(
        user=request.user,
        status__in=['open', 'escalated', 'in_review']
    ).order_by('-created_at').first()

    user_bookings = Booking.objects.filter(
        user=request.user, is_paid=True
    ).order_by('-created_at')[:10]

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'new_ticket':
            subject    = request.POST.get('subject', '').strip()
            category   = request.POST.get('category', 'other')
            booking_id = request.POST.get('booking_id')
            if not subject:
                messages.error(request, "Please enter a subject.")
                return redirect('support_chat')
            booking = None
            if booking_id:
                booking = Booking.objects.filter(pk=booking_id, user=request.user).first()
            ticket = SupportTicket.objects.create(
                user=request.user, booking=booking,
                subject=subject, category=category,
            )

            try:
                from users.emails import send_support_ack
                send_support_ack(ticket)
            except Exception:
                logger.exception(f"Support ack email failed for ticket {ticket.id}")

            SupportMessage.objects.create(
                ticket=ticket, sender_type='ai',
                content=(
                    "Thank you for reaching out to Trivasta Support! 👋\n\n"
                    "I'm your AI assistant and I'm here to help resolve your issue quickly.\n\n"
                    "**Common topics I can help with:**\n"
                    "• 💳 Payment issues and queries\n"
                    "• 📋 Booking problems and cancellations\n"
                    "• 🏢 Agency complaints\n"
                    "• 💰 Refund requests\n"
                    "• 🔧 Technical issues\n\n"
                    "Please describe your issue in detail and I'll either resolve it immediately "
                    "or connect you with a human agent."
                ),
            )
            return redirect('support_chat')

        if action == 'send_message' and ticket:
            content = request.POST.get('content', '').strip()
            if not content:
                return redirect('support_chat')

            SupportMessage.objects.create(
                ticket=ticket, sender=request.user,
                sender_type='user', content=content,
            )

            history = []
            for msg in ticket.messages.all().order_by('created_at')[:-1]:
                if msg.sender_type == 'user':
                    history.append({"role": "user", "content": msg.content})
                elif msg.sender_type in ('ai', 'agent'):
                    history.append({"role": "assistant", "content": msg.content})

            # FIX: was bare `except Exception: pass` — now logs the real error
            try:
                ai_response, needs_escalation = get_ai_support_response(content, history)
            except Exception:
                logger.exception(
                    f"AI support response failed for ticket {ticket.id} — "
                    f"falling back to generic message"
                )
                ai_response = (
                    "I'm having trouble connecting right now. "
                    "Please try again in a moment or our team will assist you shortly."
                )
                needs_escalation = False

            SupportMessage.objects.create(
                ticket=ticket, sender_type='ai', content=ai_response,
            )

            if needs_escalation and not ticket.is_escalated:
                ticket.is_escalated = True
                ticket.status       = 'escalated'
                ticket.escalated_at = timezone.now()
                ticket.save(update_fields=['is_escalated', 'status', 'escalated_at'])
                SupportMessage.objects.create(
                    ticket=ticket, sender_type='system',
                    content=(
                        "🚨 **This ticket has been escalated to our support team.**\n"
                        "A human agent will review your case within 2 hours and respond here."
                    ),
                )
            return redirect('support_chat')

    chat_messages = ticket.messages.all() if ticket else []
    return render(request, 'support/support_chat.html', {
        'ticket':        ticket,
        'chat_messages': chat_messages,
        'user_bookings': user_bookings,
        'categories':    SupportTicket.CATEGORY_CHOICES,
    })


# ─────────────────────────────────────────────────────────────────────────────
# Refund Request
# ─────────────────────────────────────────────────────────────────────────────

@login_required
def request_refund(request, booking_id):
    booking = get_object_or_404(Booking, pk=booking_id, user=request.user, is_paid=True)
    existing = RefundRequest.objects.filter(
        booking=booking, status__in=['pending', 'approved']
    ).first()
    if existing:
        messages.info(request, "A refund request for this booking is already in progress.")
        return redirect('support_chat')

    if request.method == 'POST':
        reason      = request.POST.get('reason', 'other')
        description = request.POST.get('description', '').strip()
        amount_str  = request.POST.get('amount', str(booking.total_amount))
        try:
            amount = int(amount_str)
            if amount <= 0 or amount > booking.total_amount:
                raise ValueError
        except ValueError:
            messages.error(request, "Invalid refund amount.")
            return redirect('request_refund', booking_id=booking_id)

        ticket = SupportTicket.objects.create(
            user=request.user, booking=booking,
            subject=f"Refund request — Booking #{booking.id}",
            category='refund', status='escalated',
            is_escalated=True, escalated_at=timezone.now(),
        )
        RefundRequest.objects.create(
            ticket=ticket, booking=booking, requested_by=request.user,
            reason=reason, description=description, amount=amount,
        )
        SupportMessage.objects.create(
            ticket=ticket, sender_type='system',
            content=(
                f"💰 **Refund Request Submitted**\n"
                f"Amount: ₹{amount:,}\n"
                f"Reason: {dict(RefundRequest.REASON_CHOICES).get(reason, reason)}\n"
                f"Booking: #{booking.id}\n\n"
                f"Our refund team will review this within 2 hours and process within 5-7 business days."
            ),
        )
        messages.success(request, "Refund request submitted. Our team will review within 2 hours.")
        return redirect('support_chat')

    return render(request, 'support/request_refund.html', {
        'booking': booking,
        'reasons': RefundRequest.REASON_CHOICES,
    })


# ─────────────────────────────────────────────────────────────────────────────
# Package Search
# ─────────────────────────────────────────────────────────────────────────────

def package_search(request):
    qs = Package.objects.filter(is_active=True).select_related('agency')
    q         = request.GET.get('q', '').strip()
    category  = request.GET.get('category', '')
    min_price = request.GET.get('min_price', '')
    max_price = request.GET.get('max_price', '')
    min_days  = request.GET.get('min_days', '')
    max_days  = request.GET.get('max_days', '')
    sort      = request.GET.get('sort', 'popular')

    if q:
        qs = qs.filter(
            Q(title__icontains=q) | Q(destination__icontains=q) |
            Q(description__icontains=q) | Q(agency__name__icontains=q)
        )
    if category:  qs = qs.filter(category=category)
    if min_price: qs = qs.filter(price__gte=int(min_price))
    if max_price: qs = qs.filter(price__lte=int(max_price))
    if min_days:  qs = qs.filter(duration__gte=int(min_days))
    if max_days:  qs = qs.filter(duration__lte=int(max_days))

    sort_map = {
        'price_asc':  'price', 'price_desc': '-price',
        'duration':   'duration', 'newest': '-created_at',
    }
    qs = qs.order_by(sort_map.get(sort, '-created_at'))
    return render(request, 'marketplace/package_search.html', {
        'packages': qs, 'q': q, 'category': category,
        'min_price': min_price, 'max_price': max_price,
        'min_days': min_days, 'max_days': max_days,
        'sort': sort, 'categories': Package.CATEGORY_CHOICES, 'count': qs.count(),
    })


# ─────────────────────────────────────────────────────────────────────────────
# Package Detail
# ─────────────────────────────────────────────────────────────────────────────

def package_detail(request, pk):
    package = get_object_or_404(Package, pk=pk, is_active=True)
    ip = request.META.get('HTTP_X_FORWARDED_FOR', request.META.get('REMOTE_ADDR', ''))
    if ip and ',' in ip:
        ip = ip.split(',')[0].strip()
    today = timezone.now().date()
    if request.user.is_authenticated:
        if not PackageView.objects.filter(package=package, user=request.user, created_at__date=today).exists():
            PackageView.objects.create(package=package, user=request.user, ip_address=ip)
    else:
        if not PackageView.objects.filter(package=package, ip_address=ip, created_at__date=today).exists():
            PackageView.objects.create(package=package, ip_address=ip)

    pricing    = compute_pricing(package.price)
    images     = package.images.all()
    reviews    = package.reviews.select_related('user').all()
    avg_rating = reviews.aggregate(avg=Avg('rating'))['avg'] or 0

    can_review = already_reviewed = False
    review_form = None
    if request.user.is_authenticated:
        has_booking      = Booking.objects.filter(package=package, user=request.user, is_paid=True).exists()
        already_reviewed = PackageReview.objects.filter(package=package, user=request.user).exists()
        can_review       = has_booking and not already_reviewed
        if request.method == 'POST' and can_review:
            review_form = PackageReviewForm(request.POST)
            if review_form.is_valid():
                rev = review_form.save(commit=False)
                rev.package = package
                rev.user    = request.user
                rev.save()
                messages.success(request, "Review submitted!")
                return redirect('package_detail', pk=pk)
        else:
            review_form = PackageReviewForm()

    return render(request, 'marketplace/package_detail.html', {
        'package': package, 'pricing': pricing, 'images': images,
        'reviews': reviews, 'avg_rating': round(avg_rating, 1),
        'review_count': reviews.count(), 'can_review': can_review,
        'already_reviewed': already_reviewed, 'review_form': review_form,
    })


# ─────────────────────────────────────────────────────────────────────────────
# Package Chat
# ─────────────────────────────────────────────────────────────────────────────

@login_required
def package_chat(request, pk):
    package = get_object_or_404(Package, pk=pk, is_active=True)
    room, created = ChatRoom.objects.get_or_create(
        package=package, user=request.user, agency=package.agency,
        defaults={'offer': None}
    )
    if created:
        Message.objects.create(
            room=room, sender_type='agency',
            content=(
                f"👋 Hello! Thanks for your interest in **{package.title}** to {package.destination}.\n\n"
                f"📅 Duration: {package.duration} days\n"
                f"💰 Starting from ₹{package.price:,}\n\n"
                "Feel free to ask any questions about the itinerary, inclusions, or pricing!"
            )
        )
    return redirect('chat_room', room_id=room.id)


# ─────────────────────────────────────────────────────────────────────────────
# Package Book  (legacy flow — kept for backward compat)
# New flow: book_package (below) which supports coupons
# ─────────────────────────────────────────────────────────────────────────────

@login_required
def package_book(request, pk):
    package = get_object_or_404(Package, pk=pk, is_active=True)
    pricing = compute_pricing(package.price)

    # FIX: use payment_service client, not a module-level duplicate
    from .payment_service import razorpay_client
    razorpay_order = razorpay_client.order.create({
        "amount":          pricing['total_amount'] * 100,
        "currency":        "INR",
        "payment_capture": 1,
        "notes":           {"booking_type": "package", "package_id": package.id}
    })

    booking, _ = Booking.objects.get_or_create(
        user=request.user, package=package, is_paid=False,
        defaults={**pricing, 'razorpay_order_id': razorpay_order['id']}
    )
    booking.razorpay_order_id = razorpay_order['id']
    booking.save()

    return render(request, 'marketplace/package_checkout.html', {
        'package':           package,
        'pricing':           pricing,
        'razorpay_key':      settings.RAZORPAY_KEY_ID,
        'razorpay_order_id': razorpay_order['id'],
        'user':              request.user,
    })


@agency_required
def package_toggle(request, pk):
    agency  = get_agency(request)
    package = get_object_or_404(Package, pk=pk, agency=agency)
    package.is_active = not package.is_active
    package.save(update_fields=['is_active'])
    messages.success(request, f"Package {'activated' if package.is_active else 'paused'}.")
    return redirect('agency_dashboard')


# ─────────────────────────────────────────────────────────────────────────────
# Payment success — legacy package_book flow
# ─────────────────────────────────────────────────────────────────────────────

@csrf_exempt
def package_book_success(request, pk):
    package = get_object_or_404(Package, pk=pk)
    if request.method != 'POST':
        return redirect('dashboard')
 
    order_id   = request.POST.get('razorpay_order_id')
    payment_id = request.POST.get('razorpay_payment_id')
    signature  = request.POST.get('razorpay_signature')
 
    if not verify_payment_signature(order_id, payment_id, signature):
        logger.warning(f"package_book_success: invalid signature for order {order_id}")
        return redirect('payment_failed')
 
    try:
        booking = Booking.objects.get(razorpay_order_id=order_id)
    except Booking.DoesNotExist:
        logger.error(f"package_book_success: no booking for order {order_id}")
        return redirect('payment_failed')
 
    if not booking.is_paid:
        booking.is_paid             = True
        booking.status              = 'confirmed'
        booking.razorpay_payment_id = payment_id
        booking.save()
 
        # ── Send booking confirmation email ───────────────────────────────────
        try:
            from users.emails import send_booking_confirmation
            send_booking_confirmation(booking)
        except Exception:
            pass
        # ─────────────────────────────────────────────────────────────────────
 
    amounts = _amounts_from_pricing(compute_pricing(package.price))
    payout, error = transfer_to_agency(booking, amounts)
    if error:
        logger.warning(f"Payout queued (failed) for booking {booking.id}: {error}")
 
    room, _ = ChatRoom.objects.get_or_create(
        package=package, user=booking.user, agency=package.agency,
        defaults={'offer': None}
    )
    agency = package.agency
    Message.objects.create(
        room=room, sender_type='system',
        content=(
            f"✅ Payment of ₹{booking.total_amount:,} confirmed for **{package.title}**!\n\n"
            f"🔓 Agency contact details are now unlocked:\n"
            f"📞 {agency.phone}\n📧 {agency.email}"
            + (f"\n🌐 {agency.website}" if agency.website else "") +
            "\n\nOur team will contact you within 24 hours."
        )
    )
    return redirect('package_booking_confirmation', booking_id=booking.id)


@login_required
def package_booking_confirmation(request, booking_id):
    booking = get_object_or_404(Booking, pk=booking_id, user=request.user, is_paid=True)
    package = booking.package
    agency  = package.agency if package else None
    room    = None
    if package:
        room = ChatRoom.objects.filter(package=package, user=request.user).first()
    return render(request, 'marketplace/package_confirmation.html', {
        'booking': booking, 'package': package, 'agency': agency, 'room': room,
        'pricing': {
            'base_amount':  booking.base_amount,
            'gst_amount':   booking.gst_amount,
            'total_amount': booking.total_amount,
        },
    })


# ─────────────────────────────────────────────────────────────────────────────
# Agency Registration / Auth
# ─────────────────────────────────────────────────────────────────────────────

def agency_register(request):
    if request.method == 'POST':
        form = AgencyRegisterForm(request.POST)
        if form.is_valid():
            d = form.cleaned_data
            user = User.objects.create_user(
                username=d['username'], email=d['email'], password=d['password']
            )
            Agency.objects.create(
                user=user, name=d['name'], email=d['email'], phone=d['phone'],
                description=d['description'], location=d['location'],
                website=d.get('website', ''),
                plan=request.POST.get('plan', 'professional'),
                status='pending'
            )
            messages.success(request, "Application submitted! We'll review and approve within 24 hours.")
            return redirect('agency_login')
    else:
        form = AgencyRegisterForm()
    return render(request, 'marketplace/agency_register.html', {'form': form})


def agency_subscribe(request):
    if not request.user.is_authenticated:
        return redirect('agency_login')
    agency = get_object_or_404(Agency, user=request.user)
    if agency.status == 'pending':
        return render(request, 'marketplace/agency_pending.html', {'agency': agency})
    if agency.status == 'approved':
        return redirect('agency_dashboard')
    return redirect('agency_login')


@csrf_exempt
def agency_payment_success(request):
    if request.method == "POST":
        order_id   = request.POST.get("razorpay_order_id")
        payment_id = request.POST.get("razorpay_payment_id")
        signature  = request.POST.get("razorpay_signature")
        if not verify_payment_signature(order_id, payment_id, signature):
            return redirect('agency_payment_failed')
        try:
            agency = Agency.objects.get(subscription_order_id=order_id)
            agency.subscription_paid       = True
            agency.subscription_payment_id = payment_id
            agency.subscription_expires_at = timezone.now() + timedelta(days=365)
            agency.save()
            return redirect('agency_dashboard')
        except Exception:
            return redirect('agency_payment_failed')
    return redirect('agency_login')


def agency_payment_failed(request):
    return render(request, 'marketplace/agency_payment_failed.html')


def agency_login(request):
    if request.method == 'POST':
        user = authenticate(
            request,
            username=request.POST.get('username', '').strip(),
            password=request.POST.get('password', '')
        )
        if user:
            get_object_or_404(Agency, user=user)
            login(request, user)
            return redirect('agency_dashboard')
        messages.error(request, "Invalid credentials.")
    return render(request, 'marketplace/agency_login.html')


def agency_logout(request):
    logout(request)
    return redirect('agency_login')


# ─────────────────────────────────────────────────────────────────────────────
# Agency Dashboard
# ─────────────────────────────────────────────────────────────────────────────

@agency_required
def agency_dashboard(request):
    from django.db.models import Count
    agency   = get_agency(request)
    packages = Package.objects.filter(agency=agency).order_by('-created_at')
    offers   = Offer.objects.filter(agency=agency).order_by('-created_at')
    bookings = Booking.objects.filter(
        Q(offer__agency=agency) | Q(package__agency=agency), is_paid=True
    ).select_related('offer__trip', 'offer__chatroom', 'package', 'user').order_by('-created_at')

    trips     = Trip.objects.all().order_by('-created_at')[:30]
    chatrooms = ChatRoom.objects.filter(agency=agency, is_active=True).select_related(
        'offer__trip', 'package', 'user'
    ).order_by('-created_at')

    revenue             = sum(b.agency_payout or b.total_amount for b in bookings)
    sent_offer_trip_ids = set(offers.values_list('trip_id', flat=True))

    package_stats = []
    for pkg in packages:
        total_views    = pkg.views.count()
        total_bookings = Booking.objects.filter(package=pkg, is_paid=True).count()
        total_chats    = pkg.chatrooms.count()
        conversion     = round((total_bookings / total_views * 100), 1) if total_views else 0
        pkg_revenue    = sum(
            b.agency_payout or b.total_amount
            for b in Booking.objects.filter(package=pkg, is_paid=True)
        )
        package_stats.append({
            'pkg': pkg, 'views': total_views, 'bookings': total_bookings,
            'chats': total_chats, 'conversion': conversion, 'revenue': pkg_revenue,
        })

    try:
        bank = agency.bank_details
    except Exception:
        bank = None

    return render(request, 'marketplace/agency_dashboard.html', {
        'agency': agency, 'packages': packages, 'package_stats': package_stats,
        'offers': offers, 'bookings': bookings, 'trips': trips,
        'chatrooms': chatrooms, 'revenue': revenue,
        'sent_offer_trip_ids': sent_offer_trip_ids, 'bank': bank,
    })


# ─────────────────────────────────────────────────────────────────────────────
# Chat Room
# ─────────────────────────────────────────────────────────────────────────────

@login_required
def chat_room(request, room_id):
    room     = get_object_or_404(ChatRoom, pk=room_id)
    is_user  = room.user == request.user
    is_agency = hasattr(request.user, 'agency') and request.user.agency == room.agency

    if not is_user and not is_agency:
        return redirect('dashboard')

    if is_user:
        room.messages.filter(sender_type='agency', is_read=False).update(is_read=True)
    else:
        room.messages.filter(sender_type='user', is_read=False).update(is_read=True)

    messages_qs = room.messages.select_related('payment_request').all()

    show_contacts = False
    if room.offer:
        show_contacts = Booking.objects.filter(offer=room.offer, is_paid=True).exists()
    elif room.package:
        show_contacts = Booking.objects.filter(
            package=room.package, user=room.user, is_paid=True
        ).exists()

    pending_preq = room.payment_requests.filter(status='pending').first()
    agency_trip_count = Booking.objects.filter(
        Q(offer__agency=room.agency) | Q(package__agency=room.agency), is_paid=True
    ).count()

    return render(request, 'marketplace/chat_room.html', {
        'room': room, 'messages': messages_qs,
        'is_user': is_user, 'is_agency': is_agency,
        'pending_preq': pending_preq, 'show_contacts': show_contacts,
        'offer': room.offer, 'agency_trip_count': agency_trip_count,
        'agency_review_count': 0,
    })


@login_required
def send_message(request, room_id):
    if request.method != 'POST':
        return JsonResponse({'error': 'POST only'}, status=405)

    room      = get_object_or_404(ChatRoom, pk=room_id)
    is_user   = room.user == request.user
    is_agency = hasattr(request.user, 'agency') and request.user.agency == room.agency

    if not is_user and not is_agency:
        return JsonResponse({'error': 'Forbidden'}, status=403)

    try:
        data    = json.loads(request.body)
        content = data.get('content', '').strip()
    except Exception:
        content = request.POST.get('content', '').strip()

    if not content:
        return JsonResponse({'error': 'Empty message'}, status=400)

    sender_type = 'user' if is_user else 'agency'

    if is_agency and is_violation(content):
        violation_type = classify_violation(content)
        agency         = room.agency
        AgencyWarning.objects.create(
            agency=agency, room=room,
            reason='contact_sharing', flagged_content=content
        )
        warning_count = AgencyWarning.objects.filter(agency=agency).count()
        _issue_warning_messages(room, agency, warning_count)
        return JsonResponse({
            'blocked': True, 'warning': warning_count,
            'message': f"Message blocked — contact sharing detected ({violation_type}). Warning {warning_count}/3."
        }, status=403)

    msg = Message.objects.create(room=room, sender_type=sender_type, content=content)
    return JsonResponse({
        'id': msg.id, 'content': msg.content,
        'sender_type': sender_type,
        'created_at': msg.created_at.strftime('%H:%M'),
    })


@login_required
def open_chat(request, offer_id):
    offer = get_object_or_404(Offer, pk=offer_id, trip__user=request.user)
    room, created = ChatRoom.objects.get_or_create(
        offer=offer, defaults={'user': request.user, 'agency': offer.agency}
    )
    if created:
        Message.objects.create(
            room=room, sender_type='agency',
            content=(
                f"👋 Hello! Thanks for your interest in our offer for {offer.trip.destination}.\n\n"
                "Feel free to ask any questions about the itinerary, accommodation, activities or pricing!"
            )
        )
    return redirect('chat_room', room_id=room.id)


@login_required
def approve_offer(request, offer_id):
    offer = get_object_or_404(Offer, pk=offer_id, trip__user=request.user)
    room, created = ChatRoom.objects.get_or_create(
        offer=offer, defaults={'user': request.user, 'agency': offer.agency}
    )
    if created:
        Message.objects.create(
            room=room, sender_type='agency',
            content=f"👋 Thank you for approving our offer for {offer.trip.destination}."
        )
    return redirect('chat_room', room_id=room.id)


# ─────────────────────────────────────────────────────────────────────────────
# Chat Payment Request
# ─────────────────────────────────────────────────────────────────────────────

@login_required
def raise_payment_request(request, room_id):
    if request.method != 'POST':
        return JsonResponse({'error': 'POST only'}, status=405)
    room = get_object_or_404(ChatRoom, pk=room_id)
    if not (hasattr(request.user, 'agency') and request.user.agency == room.agency):
        return JsonResponse({'error': 'Forbidden'}, status=403)

    data   = json.loads(request.body)
    amount = int(data.get('amount', 0))
    note   = data.get('note', '').strip()
    if amount <= 0:
        return JsonResponse({'error': 'Invalid amount'}, status=400)

    # Cancel any previously pending request in this room
    room.payment_requests.filter(status='pending').update(status='rejected')

    msg = Message.objects.create(
        room=room, sender_type='agency',
        content=f"💳 Payment Request: ₹{amount:,}\n{note}",
        is_payment_request=True,
    )
    pr = PaymentRequest.objects.create(room=room, message=msg, amount=amount, note=note)
    return JsonResponse({
        'id': msg.id, 'pr_id': pr.id,
        'amount': amount, 'note': note, 'status': 'pending',
    })



@login_required
def accept_payment_request(request, pr_id):
    pr      = get_object_or_404(PaymentRequest, pk=pr_id, room__user=request.user, status='pending')
    room    = pr.room
    pricing = compute_pricing(pr.amount)

    from .payment_service import razorpay_client
    razorpay_order = razorpay_client.order.create({
        "amount":          pricing['total_amount'] * 100,
        "currency":        "INR",
        "payment_capture": 1,
        "notes":           {"payment_request_id": pr.id, "room_id": room.id}
    })
    pr.razorpay_order_id = razorpay_order['id']
    pr.save()

    return render(request, 'marketplace/chat_checkout.html', {
        'pr': pr, 'room': room, 'pricing': pricing,
        'razorpay_key': settings.RAZORPAY_KEY_ID,
        'razorpay_order_id': razorpay_order['id'],
        'user': request.user,
    })


@csrf_exempt
def chat_payment_success(request):
    """
    Handles Razorpay callback for chat payment requests.
    Verifies signature → marks PaymentRequest paid → creates/updates Booking
    → fires agency payout via Razorpay Route → unlocks contact details.
    """
    if request.method != 'POST':
        return redirect('dashboard')

    order_id   = request.POST.get('razorpay_order_id')
    payment_id = request.POST.get('razorpay_payment_id')
    signature  = request.POST.get('razorpay_signature')

    if not verify_payment_signature(order_id, payment_id, signature):
        logger.warning(f"chat_payment_success: invalid signature for order {order_id}")
        return redirect('payment_failed')

    try:
        pr = PaymentRequest.objects.get(razorpay_order_id=order_id)
    except PaymentRequest.DoesNotExist:
        logger.error(f"chat_payment_success: no PaymentRequest for order {order_id}")
        return redirect('payment_failed')

    # Mark payment request as paid
    pr.status              = 'paid'
    pr.razorpay_payment_id = payment_id
    pr.save()

    room    = pr.room
    pricing = compute_pricing(pr.amount)

    # Create or update the booking
    booking, created = Booking.objects.get_or_create(
        user=room.user, offer=room.offer,
        defaults={
            **pricing,
            'is_paid':             True,
            'status':              'confirmed',
            'razorpay_order_id':   order_id,
            'razorpay_payment_id': payment_id,
        }
    )
    if not booking.is_paid:
        booking.is_paid             = True
        booking.status              = 'confirmed'
        booking.razorpay_payment_id = payment_id
        booking.commission_amount   = pricing['commission_amount']
        booking.agency_payout       = pricing['agency_payout']
        booking.save()

    # ── Fire agency payout ────────────────────────────────────────────────────
    # This is the critical part — chat payments also trigger the 10% split payout
    amounts = _amounts_from_pricing(pricing)
    payout, error = transfer_to_agency(booking, amounts)
    if error:
        logger.warning(f"Payout queued (failed) for chat booking {booking.id}: {error}")

    # ── Unlock contact details in chat ────────────────────────────────────────
    agency = room.agency
    Message.objects.create(
        room=room, sender_type='system',
        content=(
            f"✅ Payment of ₹{pricing['total_amount']:,} confirmed!\n\n"
            f"🔓 Contact details now unlocked:\n"
            f"📞 {agency.phone}\n📧 {agency.email}"
            + (f"\n🌐 {agency.website}" if agency.website else "") +
            "\n\nOur team will reach out within 24 hours."
        )
    )
    return redirect('chat_room', room_id=room.id)


@login_required
def reject_payment_request(request, pr_id):
    pr = get_object_or_404(PaymentRequest, pk=pr_id, room__user=request.user, status='pending')
    pr.status = 'rejected'
    pr.save()
    Message.objects.create(
        room=pr.room, sender_type='user',
        content="❌ I've declined this payment request. Let's negotiate further."
    )
    return redirect('chat_room', room_id=pr.room.id)


# ─────────────────────────────────────────────────────────────────────────────
# Offers
# ─────────────────────────────────────────────────────────────────────────────

@login_required
def offers(request, trip_id):
    trip        = get_object_or_404(Trip, pk=trip_id, user=request.user)
    offers_list = Offer.objects.filter(trip=trip).select_related('agency').order_by('price')
    return render(request, 'marketplace/offers.html', {'trip': trip, 'offers': offers_list})



@login_required
def checkout(request, offer_id):
    offer = get_object_or_404(Offer, pk=offer_id)
    if Booking.objects.filter(offer=offer, is_paid=True).exists():
        return redirect('dashboard')

    pricing = compute_pricing(offer.price)

    from .payment_service import razorpay_client
    razorpay_order = razorpay_client.order.create({
        "amount":          pricing['total_amount'] * 100,
        "currency":        "INR",
        "payment_capture": 1,
        "notes":           {"booking_type": "trip", "offer_id": offer_id}
    })

    booking, _ = Booking.objects.get_or_create(
        user=request.user, offer=offer,
        defaults={**pricing, 'is_paid': False, 'razorpay_order_id': razorpay_order['id']}
    )
    if booking.razorpay_order_id != razorpay_order['id']:
        booking.razorpay_order_id = razorpay_order['id']
        booking.save()

    return render(request, 'marketplace/checkout.html', {
        'offer': offer, 'pricing': pricing,
        'razorpay_key': settings.RAZORPAY_KEY_ID,
        'razorpay_order_id': razorpay_order['id'],
        'user': request.user,
    })


@csrf_exempt
def offer_payment_success(request):
    """
    Handles payment success for the trip offer checkout flow.
    Verifies signature → marks booking paid → fires agency payout.
    """
    if request.method != 'POST':
        return redirect('dashboard')

    order_id   = request.POST.get('razorpay_order_id')
    payment_id = request.POST.get('razorpay_payment_id')
    signature  = request.POST.get('razorpay_signature')

    if not verify_payment_signature(order_id, payment_id, signature):
        logger.warning(f"offer_payment_success: invalid signature for order {order_id}")
        return redirect('payment_failed')

    try:
        booking = Booking.objects.get(razorpay_order_id=order_id)
    except Booking.DoesNotExist:
        logger.error(f"offer_payment_success: no booking for order {order_id}")
        return redirect('payment_failed')

    if not booking.is_paid:
        booking.is_paid             = True
        booking.status              = 'confirmed'
        booking.razorpay_payment_id = payment_id
        booking.save()

    # ── Fire payout ───────────────────────────────────────────────────────────
    amounts = _amounts_from_pricing(compute_pricing(booking.base_amount or booking.total_amount))
    payout, error = transfer_to_agency(booking, amounts)
    if error:
        logger.warning(f"Payout queued for offer booking {booking.id}: {error}")

    return redirect('booking_confirmation', booking_id=booking.id)


def payment_failed(request):
    return render(request, 'marketplace/payment_failed.html')


@login_required
def booking_confirmation(request, booking_id):
    booking = get_object_or_404(Booking, pk=booking_id, user=request.user)
    return render(request, 'marketplace/confirmation.html', {'booking': booking})


@login_required
def trip_tracking(request, booking_id):
    booking = get_object_or_404(Booking, pk=booking_id, user=request.user, is_paid=True)
    try:
        trip_status = booking.trip_status
    except TripStatus.DoesNotExist:
        trip_status = None
    history = trip_status.updates.all() if trip_status else []
    return render(request, 'marketplace/trip_tracking.html', {
        'booking': booking, 'trip_status': trip_status, 'history': history,
    })


@agency_required
def update_trip_status(request, booking_id):
    agency  = get_agency(request)
    booking = get_object_or_404(Booking, pk=booking_id, is_paid=True)
    if booking.offer and booking.offer.agency != agency:
        return redirect('agency_dashboard')
    if booking.package and booking.package.agency != agency:
        return redirect('agency_dashboard')
 
    trip_status, _ = TripStatus.objects.get_or_create(
        booking=booking, defaults={'status': 'confirmed'}
    )
 
    if request.method == 'POST':
        new_status = request.POST.get('status')
        note       = request.POST.get('note', '').strip()
        valid      = [s[0] for s in TripStatus.STATUS_CHOICES]
        if new_status not in valid:
            messages.error(request, 'Invalid status.')
            return redirect('update_trip_status', booking_id=booking_id)
 
        TripUpdate.objects.create(trip_status=trip_status, status=new_status, note=note)
        trip_status.status = new_status
        trip_status.note   = note
        trip_status.save()
 
        # ── Send trip status update email ─────────────────────────────────────
        try:
            from users.emails import send_trip_status_update
            send_trip_status_update(booking, new_status, note)
        except Exception:
            pass
        # ─────────────────────────────────────────────────────────────────────
 
        if new_status == 'completed':
            booking.status = 'completed'
            booking.save(update_fields=['status'])
 
        try:
            room = booking.offer.chatroom if booking.offer else None
            if not room and booking.package:
                room = ChatRoom.objects.filter(
                    package=booking.package, user=booking.user
                ).first()
            if room:
                status_label = dict(TripStatus.STATUS_CHOICES).get(new_status, new_status)
                Message.objects.create(
                    room=room, sender_type='system',
                    content=f"📍 Trip Update: {status_label}\n{note}".strip()
                )
        except Exception:
            pass
 
        messages.success(request, 'Trip status updated.')
        return redirect('agency_dashboard')
 
    history = trip_status.updates.all()
    return render(request, 'marketplace/update_trip_status.html', {
        'booking': booking, 'trip_status': trip_status,
        'history': history, 'choices': TripStatus.STATUS_CHOICES,
    })


# ─────────────────────────────────────────────────────────────────────────────
# Package CRUD
# ─────────────────────────────────────────────────────────────────────────────

@agency_required
def package_create(request):
    agency = get_agency(request)
    if request.method == 'POST':
        form    = PackageForm(request.POST)
        formset = PackageImageFormSet(request.POST, request.FILES)
        if form.is_valid() and formset.is_valid():
            pkg        = form.save(commit=False)
            pkg.agency = agency
            pkg.save()
            formset.instance = pkg
            formset.save()
            messages.success(request, "Package created!")
            return redirect('agency_dashboard')
    else:
        form    = PackageForm()
        formset = PackageImageFormSet()
    return render(request, 'marketplace/package_form.html', {
        'form': form, 'formset': formset, 'action': 'Create'
    })


@agency_required
def package_edit(request, pk):
    agency  = get_agency(request)
    package = get_object_or_404(Package, pk=pk, agency=agency)
    if request.method == 'POST':
        form    = PackageForm(request.POST, instance=package)
        formset = PackageImageFormSet(request.POST, request.FILES, instance=package)
        if form.is_valid() and formset.is_valid():
            form.save()
            formset.save()
            messages.success(request, "Package updated!")
            return redirect('agency_dashboard')
    else:
        form    = PackageForm(instance=package)
        formset = PackageImageFormSet(instance=package)
    return render(request, 'marketplace/package_form.html', {
        'form': form, 'formset': formset, 'action': 'Edit'
    })


@agency_required
def package_delete(request, pk):
    agency  = get_agency(request)
    package = get_object_or_404(Package, pk=pk, agency=agency)
    package.delete()
    messages.success(request, "Package deleted.")
    return redirect('agency_dashboard')


@agency_required
def send_offer(request, trip_id):
    agency = get_agency(request)
    trip   = get_object_or_404(Trip, pk=trip_id)
    if Offer.objects.filter(agency=agency, trip=trip).exists():
        messages.warning(request, "You already sent an offer for this trip.")
        return redirect('agency_dashboard')
    if request.method == 'POST':
        form = OfferForm(request.POST)
        if form.is_valid():
            offer        = form.save(commit=False)
            offer.agency = agency
            offer.trip   = trip
            offer.save()
            messages.success(request, "Offer sent!")
            return redirect('agency_dashboard')
    else:
        form = OfferForm()
    return render(request, 'marketplace/send_offer.html', {'form': form, 'trip': trip})


@agency_required
def agency_profile(request):
    return render(request, 'marketplace/agency_profile.html', {'agency': get_agency(request)})


@agency_required
def agency_profile_edit(request):
    agency = get_agency(request)
    if request.method == 'POST':
        form = AgencyProfileForm(request.POST, instance=agency)
        if form.is_valid():
            form.save()
            messages.success(request, "Profile updated!")
            return redirect('agency_profile')
    else:
        form = AgencyProfileForm(instance=agency)
    return render(request, 'marketplace/agency_profile_edit.html', {
        'agency': agency, 'form': form
    })


# ─────────────────────────────────────────────────────────────────────────────
# Contact Guard Warning Helper
# ─────────────────────────────────────────────────────────────────────────────

def _issue_warning_messages(room, agency, warning_count):
    WARNING_TEMPLATES = {
        1: "⚠️ **Trivasta Warning (1/3) — {agency}**\n\nAn attempt to share personal contact details was detected and blocked.\n\n📋 You have **2 warnings remaining** before your plan is downgraded.",
        2: "⚠️ **Trivasta Warning (2/3) — {agency}**\n\nA second violation detected. This is your final warning.\n\n🚨 One more violation = immediate downgrade to Starter.",
        3: "🚫 **Trivasta Action Taken — {agency}**\n\nThree violations recorded. **Your plan has been downgraded to Starter.**\n\n❌ Contact Trivasta support to appeal.",
    }
    key  = min(warning_count, 3)
    text = WARNING_TEMPLATES[key].format(agency=agency.name)
    Message.objects.create(room=room, sender_type='system', content=text)
    Message.objects.create(
        room=room, sender_type='system',
        content=f"🔒 **Trivasta Notice:** A message from {agency.name} was removed — contact details are shared automatically after payment."
    )
    if warning_count >= 3:
        agency.plan = 'starter'
        agency.save(update_fields=['plan'])


# ─────────────────────────────────────────────────────────────────────────────
# Agency Bank Details & KYC
# ─────────────────────────────────────────────────────────────────────────────

@login_required
def agency_bank_details(request):
    try:
        agency = request.user.agency
    except Exception:
        messages.error(request, "You don't have an agency account.")
        return redirect('dashboard')

    try:
        bank     = agency.bank_details
        existing = True
    except Exception:
        bank     = None
        existing = False

    if request.method == 'POST':
        account_holder_name = request.POST.get('account_holder_name', '').strip()
        account_number      = request.POST.get('account_number', '').strip()
        ifsc_code           = request.POST.get('ifsc_code', '').strip().upper()
        account_type        = request.POST.get('account_type', 'current')
        bank_name           = request.POST.get('bank_name', '').strip()
        pan_number          = request.POST.get('pan_number', '').strip().upper()
        gst_number          = request.POST.get('gst_number', '').strip().upper()

        if not all([account_holder_name, account_number, ifsc_code, pan_number]):
            messages.error(request, "Account holder name, account number, IFSC and PAN are required.")
            return redirect('agency_bank_details')

        if bank:
            bank.account_holder_name = account_holder_name
            bank.account_number      = account_number
            bank.ifsc_code           = ifsc_code
            bank.account_type        = account_type
            bank.bank_name           = bank_name
            bank.pan_number          = pan_number
            bank.gst_number          = gst_number
            bank.kyc_status          = 'submitted'
            bank.save()
        else:
            AgencyBankDetails.objects.create(
                agency=agency,
                account_holder_name=account_holder_name,
                account_number=account_number,
                ifsc_code=ifsc_code,
                account_type=account_type,
                bank_name=bank_name,
                pan_number=pan_number,
                gst_number=gst_number,
                kyc_status='submitted',
            )

        messages.success(request, "Bank details submitted for KYC verification. We'll verify within 24 hours.")
        if agency.status == 'pending':
            return redirect('agency_login')
        return redirect('agency_dashboard')

    return render(request, 'marketplace/agency_bank_details.html', {
        'bank': bank, 'existing': existing, 'agency': agency,
    })


@staff_member_required
def admin_verify_kyc(request, agency_id):
    agency = get_object_or_404(Agency, pk=agency_id)
    try:
        bank = agency.bank_details
    except Exception:
        messages.error(request, "This agency has no bank details submitted.")
        return redirect('trivasta_admin')

    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'verify':
            account_id, error = create_agency_linked_account(agency)
            if error:
                messages.warning(
                    request,
                    f"Razorpay account creation failed ({error}). Marking as verified manually. "
                    f"You MUST apply for Razorpay Route at dashboard.razorpay.com → +10 More → Route."
                )
            bank.kyc_status      = 'verified'
            bank.kyc_verified_at = timezone.now()
            bank.kyc_verified_by = request.user
            bank.save(update_fields=['kyc_status', 'kyc_verified_at', 'kyc_verified_by'])
            agency.status = 'approved'
            agency.save(update_fields=['status'])
            messages.success(
                request,
                f"KYC verified for {agency.name}. Razorpay account: {account_id or 'manual (Route not active)'}"
            )
        elif action == 'reject':
            reason = request.POST.get('rejection_reason', '').strip()
            bank.kyc_status           = 'rejected'
            bank.kyc_rejection_reason = reason
            bank.save(update_fields=['kyc_status', 'kyc_rejection_reason'])
            messages.error(request, f"KYC rejected for {agency.name}.")

    return redirect('trivasta_admin')


@staff_member_required
def admin_retry_payout(request, payout_id):
    payout = get_object_or_404(PayoutRecord, pk=payout_id)
    if payout.status == 'paid':
        messages.info(request, "This payout is already completed.")
        return redirect('trivasta_admin')

    amounts = {
        'total_amount':        payout.total_amount,
        'base_amount':         payout.base_amount,
        'gst_amount':          payout.gst_amount,
        'trivasta_commission': payout.trivasta_commission,
        'agency_payout':       payout.agency_payout_amount,
        'discount_amount':     payout.discount_amount,
    }
    _, error = transfer_to_agency(payout.booking, amounts)
    if error:
        messages.error(request, f"Retry failed: {error}")
    else:
        messages.success(request, f"Payout retried for Booking #{payout.booking.id}")
    return redirect('trivasta_admin')


# ─────────────────────────────────────────────────────────────────────────────
# Coupon
# ─────────────────────────────────────────────────────────────────────────────

@login_required
def validate_coupon_ajax(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    code        = request.POST.get('code', '').strip()
    base_amount = int(request.POST.get('base_amount', 0))
    agency_id   = request.POST.get('agency_id')
    agency      = Agency.objects.filter(pk=agency_id).first() if agency_id else None

    coupon, error = validate_coupon(code, request.user, base_amount, agency)
    if error:
        return JsonResponse({'valid': False, 'error': error})

    amounts = calculate_booking_amounts(base_amount, coupon)
    return JsonResponse({
        'valid':           True,
        'code':            coupon.code,
        'discount_amount': amounts['discount_amount'],
        'base_amount':     amounts['base_amount'],
        'gst_amount':      amounts['gst_amount'],
        'total_amount':    amounts['total_amount'],
        'message':         f"✅ {coupon.discount_value}% off applied — you save ₹{amounts['discount_amount']:,}!",
    })


# ─────────────────────────────────────────────────────────────────────────────
# Book Package  (new flow — supports coupons)
# ─────────────────────────────────────────────────────────────────────────────

@login_required
def book_package(request, package_id):
    package = get_object_or_404(Package, pk=package_id, is_active=True)
    coupon  = None
    amounts = calculate_booking_amounts(package.price)

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'apply_coupon':
            code = request.POST.get('coupon_code', '').strip()
            if code:
                coupon, error = validate_coupon(code, request.user, package.price, package.agency)
                if error:
                    messages.error(request, error)
                else:
                    amounts = calculate_booking_amounts(package.price, coupon)
                    messages.success(request, f"Coupon applied! You save ₹{amounts['discount_amount']:,}")
            return render(request, 'marketplace/book_package.html', {
                'package': package, 'amounts': amounts,
                'coupon': coupon, 'RAZORPAY_KEY_ID': settings.RAZORPAY_KEY_ID,
            })

        if action == 'create_order':
            coupon_code = request.POST.get('applied_coupon', '')
            if coupon_code:
                coupon, _ = validate_coupon(coupon_code, request.user, package.price, package.agency)
                amounts   = calculate_booking_amounts(package.price, coupon)

            booking = Booking.objects.create(
                user              = request.user,
                package           = package,
                base_amount       = amounts['base_amount'],
                gst_amount        = amounts['gst_amount'],
                commission_amount = amounts['trivasta_commission'],
                agency_payout     = amounts['agency_payout'],
                total_amount      = amounts['total_amount'],
                is_paid           = False,
                status            = 'pending',
            )

            order, error = create_razorpay_order(amounts, booking.id)
            if error:
                booking.delete()
                messages.error(request, f"Payment initialization failed: {error}")
                return redirect('book_package', package_id=package_id)

            booking.razorpay_order_id = order['id']
            booking.save(update_fields=['razorpay_order_id'])

            if coupon:
                request.session[f'coupon_booking_{booking.id}'] = coupon.code

            return render(request, 'marketplace/payment_checkout.html', {
                'booking': booking, 'package': package, 'amounts': amounts,
                'coupon': coupon, 'razorpay_order': order,
                'RAZORPAY_KEY_ID': settings.RAZORPAY_KEY_ID,
            })

    return render(request, 'marketplace/book_package.html', {
        'package': package, 'amounts': amounts,
        'coupon': None, 'RAZORPAY_KEY_ID': settings.RAZORPAY_KEY_ID,
    })


# ─────────────────────────────────────────────────────────────────────────────
# Payment success — new book_package flow
# ─────────────────────────────────────────────────────────────────────────────

@csrf_exempt
def payment_success(request, booking_id):
    """
    Payment callback for the new book_package flow.
    Razorpay POSTs here after the user pays. Signature verification
    replaces CSRF as the auth mechanism.
    """
    if request.method != 'POST':
        return redirect('dashboard')

    order_id   = request.POST.get('razorpay_order_id')
    payment_id = request.POST.get('razorpay_payment_id')
    signature  = request.POST.get('razorpay_signature')

    if not verify_payment_signature(order_id, payment_id, signature):
        logger.warning(f"payment_success: invalid signature for booking {booking_id}")
        return redirect('payment_failed')

    # FIX 2: Look up by pk only, not pk + user, because Razorpay posts here
    # server-to-server and there is no logged-in user in the request context.
    try:
        booking = Booking.objects.get(pk=booking_id)
    except Booking.DoesNotExist:
        logger.error(f"payment_success: no booking with id {booking_id}")
        return redirect('payment_failed')

    # FIX 3: Idempotency — Razorpay may deliver the callback more than once.
    # Return silently instead of re-processing a booking that is already paid.
    if booking.is_paid:
        return redirect('dashboard')

    booking.is_paid             = True
    booking.status              = 'confirmed'
    booking.razorpay_payment_id = payment_id
    booking.save()

    try:
        from users.emails import send_booking_confirmation
        send_booking_confirmation(booking)
    except Exception:
        # FIX 4: log instead of silently swallowing so email failures are visible
        logger.exception(f"Booking confirmation email failed for booking {booking_id}")

    # FIX 5: was `discount_applied=booking.base_amount` which recorded the wrong
    # value. base_amount is the discounted price the customer paid, not the
    # discount itself. Recalculate using the coupon to get the actual saving.
    coupon_code = request.session.pop(f'coupon_booking_{booking.id}', None)
    if coupon_code:
        try:
            coupon = Coupon.objects.get(code=coupon_code)
            recalculated    = calculate_booking_amounts(booking.base_amount, coupon)
            actual_discount = recalculated['discount_amount']
            CouponUsage.objects.create(
                coupon           = coupon,
                user             = booking.user,
                booking          = booking,
                discount_applied = actual_discount,
            )
            coupon.mark_used()
        except Exception:
            logger.exception(f"Coupon usage recording failed for booking {booking_id}")

    amounts = {
        'total_amount':        booking.total_amount,
        'base_amount':         booking.base_amount,
        'gst_amount':          booking.gst_amount,
        'trivasta_commission': booking.commission_amount,
        'agency_payout':       booking.agency_payout,
        'discount_amount':     0,
    }
    payout, error = transfer_to_agency(booking, amounts)
    if error:
        logger.warning(f"Payout queued (failed) for booking {booking.id}: {error}")

    return redirect('dashboard')


# ─────────────────────────────────────────────────────────────────────────────
# Agency Earnings
# ─────────────────────────────────────────────────────────────────────────────

@login_required
def agency_earnings(request):
    try:
        agency = request.user.agency
    except Exception:
        messages.error(request, "You don't have an agency account.")
        return redirect('dashboard')

    payouts = PayoutRecord.objects.filter(agency=agency).order_by('-created_at')

    total_earned   = sum(p.agency_payout_amount for p in payouts if p.status == 'paid')
    total_pending  = sum(p.agency_payout_amount for p in payouts if p.status == 'pending')
    total_bookings = payouts.count()

    try:
        bank = agency.bank_details
    except Exception:
        bank = None

    return render(request, 'marketplace/agency_earnings.html', {
        'agency': agency, 'payouts': payouts,
        'total_earned': total_earned, 'total_pending': total_pending,
        'total_bookings': total_bookings, 'bank': bank,
    })
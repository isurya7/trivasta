from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.contrib.auth.models import User
from django.db.models import Sum, Count, Q, Avg
from django.db import IntegrityError
from django.contrib.admin.views.decorators import staff_member_required
from django.db.models.functions import TruncMonth
from django.utils import timezone
from datetime import timedelta
from django.core.mail import send_mail
from django.conf import settings
import json
import razorpay

from .models import Profile
from trips.models import Trip
from marketplace.models import (
    Agency, Booking, Offer, ChatRoom, AgencyWarning,
    TripStatus, Package, SupportTicket, SupportMessage, RefundRequest,
)

razorpay_client = razorpay.Client(
    auth=(
        __import__('django.conf', fromlist=['settings']).settings.RAZORPAY_KEY_ID,
        __import__('django.conf', fromlist=['settings']).settings.RAZORPAY_KEY_SECRET,
    )
)


# ── Auth ──────────────────────────────────────────────────────────────────────

def register_view(request):
    if request.method == "POST":
        username         = request.POST.get("username", "").strip()
        email            = request.POST.get("email", "").strip()
        password         = request.POST.get("password", "")
        confirm_password = request.POST.get("confirm_password", "")

        if not username or not email or not password:
            messages.error(request, "All fields are required.")
            return redirect("register")
        if password != confirm_password:
            messages.error(request, "Passwords do not match.")
            return redirect("register")
        if User.objects.filter(username=username).exists():
            messages.error(request, "Username already exists.")
            return redirect("register")
        if User.objects.filter(email=email).exists():
            messages.error(request, "Email already registered.")
            return redirect("register")

        try:
            user = User.objects.create_user(username=username, email=email, password=password)
            Profile.objects.get_or_create(user=user)
            messages.success(request, "Account created successfully. Please login.")
            return redirect("login")
        except IntegrityError:
            messages.error(request, "Something went wrong. Try again.")
            return redirect("register")

    return render(request, "users/register.html", {"page_title": "Create Account"})


def login_view(request):
    if request.method == "POST":
        username = request.POST.get("username", "").strip()
        password = request.POST.get("password", "")
        user     = authenticate(request, username=username, password=password)
        if user:
            login(request, user)
            next_url = request.GET.get("next")
            return redirect(next_url if next_url else "dashboard")
        messages.error(request, "Invalid username or password.")
        return redirect("login")
    return render(request, "users/login.html", {"page_title": "Login"})


@login_required
def logout_view(request):
    logout(request)
    messages.success(request, "Logged out successfully.")
    return redirect("home")


# ── User Dashboard ────────────────────────────────────────────────────────────

@login_required
def dashboard_view(request):
    user     = request.user
    trips    = Trip.objects.filter(user=user).order_by('-created_at')
    bookings = Booking.objects.filter(user=user, is_paid=True).select_related(
        'offer__trip', 'offer__agency', 'offer__chatroom', 'package__agency'
    ).order_by('-created_at')

    chatrooms = ChatRoom.objects.filter(user=user, is_active=True).select_related(
        'agency', 'offer__trip', 'package'
    ).prefetch_related('messages').order_by('-created_at')

    total_spent = sum(b.total_amount for b in bookings)

    featured_packages = Package.objects.filter(
        is_active=True
    ).select_related('agency').order_by('-created_at')[:6]

    open_ticket = SupportTicket.objects.filter(
        user=user, status__in=['open', 'escalated', 'in_review']
    ).first()

    return render(request, 'users/dashboard.html', {
        'trips':             trips,
        'bookings':          bookings,
        'chatrooms':         chatrooms,
        'total_spent':       total_spent,
        'featured_packages': featured_packages,
        'open_ticket':       open_ticket,
    })


# ── Trivasta Admin Dashboard ──────────────────────────────────────────────────

@staff_member_required
def trivasta_admin(request):
    total_revenue    = Booking.objects.filter(is_paid=True).aggregate(s=Sum('total_amount'))['s'] or 0
    total_gst        = Booking.objects.filter(is_paid=True).aggregate(s=Sum('gst_amount'))['s'] or 0
    total_bookings   = Booking.objects.filter(is_paid=True).count()
    total_trips      = Trip.objects.count()
    total_users      = User.objects.filter(is_staff=False).count()
    total_agencies   = Agency.objects.filter(status='approved').count()
    pending_agencies = Agency.objects.filter(status='pending').count()
    active_chats     = ChatRoom.objects.filter(is_active=True).count()

    six_months_ago = timezone.now() - timedelta(days=180)
    monthly_revenue = (
        Booking.objects.filter(is_paid=True, created_at__gte=six_months_ago)
        .annotate(month=TruncMonth('created_at'))
        .values('month').annotate(total=Sum('total_amount'), count=Count('id'))
        .order_by('month')
    )

    revenue_labels = [r['month'].strftime('%b %Y') for r in monthly_revenue]
    revenue_data   = [int(r['total']) for r in monthly_revenue]
    booking_counts = [r['count'] for r in monthly_revenue]

    top_agencies = (
        Agency.objects.filter(status='approved').annotate(
            total_revenue=Sum('offer__booking__total_amount', filter=Q(offer__booking__is_paid=True)),
            total_bookings_count=Count('offer__booking', filter=Q(offer__booking__is_paid=True)),
        ).order_by('-total_revenue')[:8]
    )

    recent_bookings = (
        Booking.objects.filter(is_paid=True)
        .select_related('user', 'offer__agency', 'offer__trip')
        .order_by('-created_at')[:10]
    )

    pending_agency_list = Agency.objects.filter(status='pending').order_by('-created_at')
    warned_agencies = (
        Agency.objects.annotate(warning_count=Count('warnings'))
        .filter(warning_count__gt=0).order_by('-warning_count')[:5]
    )

    status_dist   = TripStatus.objects.values('status').annotate(count=Count('id')).order_by('-count')
    status_labels = [s['status'].replace('_', ' ').title() for s in status_dist]
    status_counts = [s['count'] for s in status_dist]
    recent_trips  = Trip.objects.select_related('user').order_by('-created_at')[:10]

    open_tickets      = SupportTicket.objects.filter(status='open').count()
    escalated_tickets = SupportTicket.objects.filter(status='escalated').count()
    pending_refunds   = RefundRequest.objects.filter(status='pending').count()

    # Contact form messages for admin
    from .models import ContactMessage
    contact_messages = ContactMessage.objects.order_by('-created_at')[:20]

    return render(request, 'users/trivasta_admin.html', {
        'total_revenue': total_revenue, 'total_gst': total_gst,
        'total_bookings': total_bookings, 'total_trips': total_trips,
        'total_users': total_users, 'total_agencies': total_agencies,
        'pending_agencies': pending_agencies, 'active_chats': active_chats,
        'revenue_labels': json.dumps(revenue_labels),
        'revenue_data': json.dumps(revenue_data),
        'booking_counts': json.dumps(booking_counts),
        'status_labels': json.dumps(status_labels),
        'status_counts': json.dumps(status_counts),
        'top_agencies': top_agencies, 'recent_bookings': recent_bookings,
        'pending_agency_list': pending_agency_list,
        'warned_agencies': warned_agencies, 'recent_trips': recent_trips,
        'open_tickets': open_tickets, 'escalated_tickets': escalated_tickets,
        'pending_refunds': pending_refunds,
        'contact_messages': contact_messages,
    })


@staff_member_required
def admin_approve_agency(request, agency_id):
    agency        = get_object_or_404(Agency, pk=agency_id)
    agency.status = 'approved'
    agency.save(update_fields=['status'])
    messages.success(request, f'{agency.name} approved.')
    return redirect('trivasta_admin')


@staff_member_required
def admin_reject_agency(request, agency_id):
    agency        = get_object_or_404(Agency, pk=agency_id)
    agency.status = 'rejected'
    agency.save(update_fields=['status'])
    messages.success(request, f'{agency.name} rejected.')
    return redirect('trivasta_admin')


@staff_member_required
def admin_reset_warnings(request, agency_id):
    agency = get_object_or_404(Agency, pk=agency_id)
    AgencyWarning.objects.filter(agency=agency).delete()
    messages.success(request, f'Warnings cleared for {agency.name}.')
    return redirect('trivasta_admin')


# ── Support Dashboard ─────────────────────────────────────────────────────────

@staff_member_required
def support_dashboard(request):
    """
    Unified support dashboard — tickets + contact messages in one place.
    Tab switching via ?tab=tickets or ?tab=contact
    """
    tab           = request.GET.get('tab', 'tickets')
    status_filter = request.GET.get('status', 'escalated')
 
    tickets = SupportTicket.objects.filter(
        status=status_filter
    ).select_related('user', 'booking').order_by('-created_at')
 
    all_counts = {
        'open':      SupportTicket.objects.filter(status='open').count(),
        'escalated': SupportTicket.objects.filter(status='escalated').count(),
        'in_review': SupportTicket.objects.filter(status='in_review').count(),
        'resolved':  SupportTicket.objects.filter(status='resolved').count(),
    }
 
    # Contact messages
    from .models import ContactMessage
    contact_msgs  = ContactMessage.objects.select_related('user', 'booking').order_by('-created_at')
    unread_count  = ContactMessage.objects.filter(is_read=False).count()
 
    # Handle mark-as-read POST from the contact tab
    if request.method == 'POST':
        msg_id = request.POST.get('mark_read')
        if msg_id:
            ContactMessage.objects.filter(pk=msg_id).update(is_read=True)
        return redirect(f"{request.path}?tab=contact")
 
    return render(request, 'support/support_dashboard.html', {
        'tab':           tab,
        'tickets':       tickets,
        'status_filter': status_filter,
        'all_counts':    all_counts,
        'contact_msgs':  contact_msgs,
        'unread_count':  unread_count,
    })


@staff_member_required
def support_ticket_detail(request, ticket_id):
    ticket     = get_object_or_404(SupportTicket, pk=ticket_id)
    msgs       = ticket.messages.all()
    has_refund = hasattr(ticket, 'refund_request')

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'reply':
            content = request.POST.get('content', '').strip()
            if content:
                SupportMessage.objects.create(
                    ticket=ticket, sender=request.user,
                    sender_type='agent', content=content,
                )
                ticket.status = 'in_review'
                ticket.save(update_fields=['status'])
                messages.success(request, "Reply sent.")

        elif action == 'resolve':
            ticket.status      = 'resolved'
            ticket.resolved_at = timezone.now()
            ticket.save(update_fields=['status', 'resolved_at'])
            SupportMessage.objects.create(
                ticket=ticket, sender=request.user,
                sender_type='agent',
                content="✅ This ticket has been marked as resolved by our support team. Thank you for your patience.",
            )
            messages.success(request, "Ticket resolved.")

        elif action == 'create_refund':
            if not has_refund and ticket.booking:
                amount = request.POST.get('refund_amount', ticket.booking.total_amount)
                reason = request.POST.get('refund_reason', 'other')
                RefundRequest.objects.create(
                    ticket=ticket,
                    booking=ticket.booking,
                    requested_by=ticket.user,
                    reason=reason,
                    amount=int(amount),
                )
                SupportMessage.objects.create(
                    ticket=ticket, sender=request.user,
                    sender_type='system',
                    content=f"💰 Refund request of ₹{amount} created and sent to the refund team.",
                )
                messages.success(request, "Refund request created.")

        return redirect('support_ticket_detail', ticket_id=ticket_id)

    return render(request, 'support/ticket_detail.html', {
        'ticket':     ticket,
        'msgs':       msgs,
        'has_refund': has_refund,
    })


# ── Refund Dashboard ──────────────────────────────────────────────────────────

@staff_member_required
def refund_dashboard(request):
    status_filter = request.GET.get('status', 'pending')
    refunds = RefundRequest.objects.filter(
        status=status_filter
    ).select_related('booking', 'booking__user', 'requested_by', 'ticket').order_by('-created_at')

    counts = {
        'pending':   RefundRequest.objects.filter(status='pending').count(),
        'approved':  RefundRequest.objects.filter(status='approved').count(),
        'processed': RefundRequest.objects.filter(status='processed').count(),
        'rejected':  RefundRequest.objects.filter(status='rejected').count(),
    }

    return render(request, 'support/refund_dashboard.html', {
        'refunds':       refunds,
        'status_filter': status_filter,
        'counts':        counts,
    })


@staff_member_required
def process_refund(request, refund_id):
    refund  = get_object_or_404(RefundRequest, pk=refund_id)
    booking = refund.booking

    if refund.status == 'processed':
        messages.info(request, "This refund has already been processed.")
        return redirect('refund_dashboard')

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'approve':
            if not booking.razorpay_payment_id:
                messages.error(request, "No Razorpay payment ID on this booking.")
                return redirect('refund_dashboard')

            try:
                razorpay_refund = razorpay_client.payment.refund(
                    booking.razorpay_payment_id,
                    {
                        "amount": refund.amount * 100,
                        "speed":  "normal",
                        "notes":  {
                            "refund_id":  refund.id,
                            "booking_id": booking.id,
                            "reason":     refund.reason,
                        },
                    }
                )
                refund.razorpay_refund_id = razorpay_refund['id']
                refund.status             = 'processed'
                refund.processed_by       = request.user
                refund.processed_at       = timezone.now()
                refund.save()

                if refund.ticket:
                    refund.ticket.status      = 'resolved'
                    refund.ticket.resolved_at = timezone.now()
                    refund.ticket.save(update_fields=['status', 'resolved_at'])
                    SupportMessage.objects.create(
                        ticket=refund.ticket,
                        sender=request.user,
                        sender_type='system',
                        content=(
                            f"✅ **Refund Processed**\n"
                            f"₹{refund.amount:,} refund initiated to your original payment method.\n"
                            f"Razorpay Refund ID: `{razorpay_refund['id']}`\n"
                            f"Expected within 5-7 business days."
                        ),
                    )

                messages.success(request, f"Refund of ₹{refund.amount:,} processed. ID: {razorpay_refund['id']}")

            except Exception as e:
                messages.error(request, f"Razorpay refund failed: {e}")

        elif action == 'reject':
            rejection_reason        = request.POST.get('rejection_reason', '').strip()
            refund.status           = 'rejected'
            refund.rejection_reason = rejection_reason
            refund.processed_by     = request.user
            refund.processed_at     = timezone.now()
            refund.save()

            if refund.ticket:
                SupportMessage.objects.create(
                    ticket=refund.ticket,
                    sender=request.user,
                    sender_type='agent',
                    content=(
                        f"❌ **Refund Request Rejected**\n"
                        f"Reason: {rejection_reason or 'Not specified'}\n\n"
                        f"If you disagree with this decision, please reply to this ticket."
                    ),
                )
                refund.ticket.status      = 'resolved'
                refund.ticket.resolved_at = timezone.now()
                refund.ticket.save(update_fields=['status', 'resolved_at'])

            messages.success(request, "Refund request rejected.")

        return redirect('refund_dashboard')

    return render(request, 'support/process_refund.html', {'refund': refund, 'booking': booking})


# ── Contact Us ────────────────────────────────────────────────────────────────

def contact_view(request):
    """
    Public contact page. Saves message to DB and optionally emails staff.
    Staff can view all messages in the support dashboard or admin panel.
    """
    user_bookings = []
    if request.user.is_authenticated:
        user_bookings = Booking.objects.filter(
            user=request.user, is_paid=True
        ).order_by('-created_at')[:10]

    if request.method == 'POST':
        first_name = request.POST.get('first_name', '').strip()
        last_name  = request.POST.get('last_name', '').strip()
        email      = request.POST.get('email', '').strip()
        subject    = request.POST.get('subject', '').strip()
        message    = request.POST.get('message', '').strip()
        booking_id = request.POST.get('booking_id', '')

        if not first_name or not email or not subject or not message:
            messages.error(request, "Please fill in all required fields.")
            return redirect('contact')

        # Save to DB
        from .models import ContactMessage
        booking = None
        if booking_id:
            booking = Booking.objects.filter(pk=booking_id).first()

        ContactMessage.objects.create(
            first_name = first_name,
            last_name  = last_name,
            email      = email,
            subject    = subject,
            message    = message,
            booking    = booking,
            user       = request.user if request.user.is_authenticated else None,
        )

        # Email staff (silent fail if email not configured)
        try:
            send_mail(
                subject  = f"[Contact] {subject} — {first_name} {last_name}",
                message  = (
                    f"From: {first_name} {last_name} <{email}>\n"
                    f"Subject: {subject}\n"
                    f"Booking: {'#' + booking_id if booking_id else 'N/A'}\n\n"
                    f"{message}"
                ),
                from_email = settings.DEFAULT_FROM_EMAIL,
                recipient_list = [settings.EMAIL_HOST_USER],
                fail_silently  = True,
            )
        except Exception:
            pass

        messages.success(
            request,
            "Your message has been sent! Our team will get back to you within 2 hours."
        )
        return redirect('contact')

    return render(request, 'contact.html', {
        'user_bookings': user_bookings,
    })


# ── Contact Messages (staff view) ─────────────────────────────────────────────

@staff_member_required
def contact_messages_view(request):
    """
    Staff-only view to read all contact form submissions.
    """
    from .models import ContactMessage
    contact_msgs = ContactMessage.objects.select_related('user', 'booking').order_by('-created_at')

    # Mark as read when staff views
    if request.method == 'POST':
        msg_id = request.POST.get('mark_read')
        if msg_id:
            ContactMessage.objects.filter(pk=msg_id).update(is_read=True)
        return redirect('contact_messages')

    return render(request, 'support/contact_messages.html', {
        'contact_msgs': contact_msgs,
        'unread_count': ContactMessage.objects.filter(is_read=False).count(),
    })
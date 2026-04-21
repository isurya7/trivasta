from django.contrib import admin
from .models import (
    Agency, Package, Offer, Booking, ChatRoom, Message,
    PaymentRequest, AgencyWarning, TripUpdate, TripStatus,
    SupportTicket, SupportMessage, RefundRequest,
)


@admin.register(Agency)
class AgencyAdmin(admin.ModelAdmin):
    list_display  = ('name', 'email', 'phone', 'location', 'plan', 'status', 'created_at')
    list_filter   = ('status', 'plan')
    search_fields = ('name', 'email', 'phone', 'location')
    list_editable = ('status', 'plan')
    ordering      = ('-created_at',)
    actions       = ['approve_agencies', 'reject_agencies']

    def approve_agencies(self, request, queryset):
        queryset.update(status='approved')
        self.message_user(request, f"{queryset.count()} agency(ies) approved.")
    approve_agencies.short_description = "✅ Approve selected agencies"

    def reject_agencies(self, request, queryset):
        queryset.update(status='rejected')
        self.message_user(request, f"{queryset.count()} agency(ies) rejected.")
    reject_agencies.short_description = "❌ Reject selected agencies"


@admin.register(Package)
class PackageAdmin(admin.ModelAdmin):
    list_display  = ('title', 'agency', 'destination', 'price', 'duration', 'category', 'is_active')
    list_filter   = ('category', 'is_active')
    search_fields = ('title', 'destination', 'agency__name')
    list_editable = ('is_active',)
    ordering      = ('-created_at',)


@admin.register(Offer)
class OfferAdmin(admin.ModelAdmin):
    list_display  = ('agency', 'trip_destination', 'price', 'created_at')
    search_fields = ('agency__name', 'trip__destination')
    ordering      = ('-created_at',)

    def trip_destination(self, obj):
        return obj.trip.destination if obj.trip else '—'
    trip_destination.short_description = 'Destination'


@admin.register(Booking)
class BookingAdmin(admin.ModelAdmin):
    list_display    = ('id', 'user', 'destination', 'get_agency', 'base_amount', 'gst_amount', 'total_amount', 'is_paid', 'status', 'created_at')
    list_filter     = ('is_paid', 'status')
    search_fields   = ('user__username', 'offer__trip__destination', 'offer__agency__name', 'package__title', 'package__agency__name')
    readonly_fields = ('razorpay_order_id', 'razorpay_payment_id')
    ordering        = ('-created_at',)

    def destination(self, obj):
        if obj.offer and obj.offer.trip:
            return obj.offer.trip.destination
        if obj.package:
            return obj.package.destination
        return '—'
    destination.short_description = 'Destination'

    def get_agency(self, obj):
        if obj.offer and obj.offer.agency:
            return obj.offer.agency.name
        if obj.package and obj.package.agency:
            return obj.package.agency.name
        return '—'
    get_agency.short_description = 'Agency'


@admin.register(ChatRoom)
class ChatRoomAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'agency', 'get_trip', 'is_active', 'created_at')
    list_filter  = ('is_active',)
    search_fields = ('user__username', 'agency__name', 'offer__trip__destination', 'package__title')
    ordering     = ('-created_at',)

    def get_trip(self, obj):
        if obj.offer and obj.offer.trip:
            return obj.offer.trip.destination
        if obj.package:
            return obj.package.title
        return '—'
    get_trip.short_description = 'Trip / Package'


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display  = ('room', 'sender_type', 'short_content', 'is_payment_request', 'is_read', 'created_at')
    list_filter   = ('sender_type', 'is_payment_request', 'is_read')
    search_fields = ('content', 'room__user__username', 'room__agency__name')
    ordering      = ('-created_at',)

    def short_content(self, obj):
        return obj.content[:60]
    short_content.short_description = 'Content'


@admin.register(PaymentRequest)
class PaymentRequestAdmin(admin.ModelAdmin):
    list_display    = ('id', 'room', 'amount', 'status', 'razorpay_order_id', 'razorpay_payment_id', 'created_at')
    list_filter     = ('status',)
    search_fields   = ('room__user__username', 'room__agency__name')
    readonly_fields = ('razorpay_order_id', 'razorpay_payment_id')
    ordering        = ('-created_at',)
    actions         = ['mark_paid']

    def mark_paid(self, request, queryset):
        queryset.update(status='paid')
        self.message_user(request, f"{queryset.count()} payment request(s) marked as paid.")
    mark_paid.short_description = "✅ Mark selected as paid"


@admin.register(AgencyWarning)
class AgencyWarningAdmin(admin.ModelAdmin):
    list_display    = ('agency', 'reason', 'warning_number', 'room', 'created_at')
    list_filter     = ('reason',)
    search_fields   = ('agency__name',)
    readonly_fields = ('agency', 'room', 'reason', 'flagged_content', 'created_at')
    ordering        = ('-created_at',)

    def warning_number(self, obj):
        return AgencyWarning.objects.filter(
            agency=obj.agency, created_at__lte=obj.created_at
        ).count()
    warning_number.short_description = 'Warning #'


@admin.register(TripStatus)
class TripStatusAdmin(admin.ModelAdmin):
    list_display = ('get_destination', 'get_traveler', 'get_agency', 'status', 'updated_at')
    list_filter  = ('status',)
    search_fields = ('booking__user__username',)
    ordering     = ('-updated_at',)

    def get_destination(self, obj):
        if obj.booking.offer and obj.booking.offer.trip:
            return obj.booking.offer.trip.destination
        if obj.booking.package:
            return obj.booking.package.destination
        return '—'
    get_destination.short_description = 'Destination'

    def get_traveler(self, obj):
        return obj.booking.user.username
    get_traveler.short_description = 'Traveler'

    def get_agency(self, obj):
        if obj.booking.offer and obj.booking.offer.agency:
            return obj.booking.offer.agency.name
        if obj.booking.package and obj.booking.package.agency:
            return obj.booking.package.agency.name
        return '—'
    get_agency.short_description = 'Agency'


@admin.register(TripUpdate)
class TripUpdateAdmin(admin.ModelAdmin):
    list_display = ('trip_status', 'status', 'created_at')
    list_filter  = ('status',)
    ordering     = ('-created_at',)


# ── Support & Refund ──────────────────────────────────────────────────────────

@admin.register(SupportTicket)
class SupportTicketAdmin(admin.ModelAdmin):
    list_display  = ('id', 'user', 'subject', 'category', 'status', 'is_escalated', 'created_at')
    list_filter   = ('status', 'category', 'is_escalated')
    search_fields = ('user__username', 'subject')
    readonly_fields = ('created_at', 'updated_at', 'escalated_at', 'resolved_at')
    ordering      = ('-created_at',)


@admin.register(SupportMessage)
class SupportMessageAdmin(admin.ModelAdmin):
    list_display  = ('ticket', 'sender_type', 'short_content', 'created_at')
    list_filter   = ('sender_type',)
    search_fields = ('content', 'ticket__subject')
    ordering      = ('-created_at',)

    def short_content(self, obj):
        return obj.content[:80]
    short_content.short_description = 'Message'


@admin.register(RefundRequest)
class RefundRequestAdmin(admin.ModelAdmin):
    list_display    = ('id', 'get_user', 'amount', 'reason', 'status', 'razorpay_refund_id', 'created_at')
    list_filter     = ('status', 'reason')
    search_fields   = ('booking__user__username', 'razorpay_refund_id')
    readonly_fields = ('razorpay_refund_id', 'created_at', 'updated_at', 'processed_at')
    ordering        = ('-created_at',)

    def get_user(self, obj):
        return obj.requested_by.username
    get_user.short_description = 'Requested By'
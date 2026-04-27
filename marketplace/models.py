from django.db import models
from django.conf import settings
from django.contrib.auth.models import User
from trips.models import Review
from django.db.models import Avg, Count


class Agency(models.Model):
    STATUS_CHOICES = [
        ('pending',  'Pending Approval'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
    ]
    PLAN_CHOICES = [
        ('starter',      'Starter — ₹4,999/yr'),
        ('professional', 'Professional — ₹9,999/yr'),
        ('enterprise',   'Enterprise — ₹19,999/yr'),
    ]
    user        = models.OneToOneField(User, on_delete=models.CASCADE, null=True, blank=True)
    name        = models.CharField(max_length=255)
    email       = models.EmailField(unique=True, blank=True, default='')
    phone       = models.CharField(max_length=20, blank=True)
    description = models.TextField(blank=True, default='')
    location    = models.CharField(max_length=255, blank=True)
    website     = models.URLField(blank=True)
    rating      = models.FloatField(default=4.5)
    status      = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    created_at  = models.DateTimeField(auto_now_add=True, null=True)
    plan        = models.CharField(max_length=20, choices=PLAN_CHOICES, default='professional')
    subscription_paid       = models.BooleanField(default=False)
    subscription_order_id   = models.CharField(max_length=100, blank=True, null=True)
    subscription_payment_id = models.CharField(max_length=100, blank=True, null=True)
    subscription_expires_at = models.DateTimeField(null=True, blank=True)
 
    # ── Phase 3: Rating cache fields ─────────────────────────────
    avg_overall_rating       = models.DecimalField(max_digits=3, decimal_places=2, default=0.00, editable=False)
    avg_guides_rating        = models.DecimalField(max_digits=3, decimal_places=2, null=True, blank=True, editable=False)
    avg_accommodation_rating = models.DecimalField(max_digits=3, decimal_places=2, null=True, blank=True, editable=False)
    avg_value_rating         = models.DecimalField(max_digits=3, decimal_places=2, null=True, blank=True, editable=False)
    avg_transport_rating     = models.DecimalField(max_digits=3, decimal_places=2, null=True, blank=True, editable=False)
    total_review_count       = models.PositiveIntegerField(default=0, editable=False)
 
    def update_rating_cache(self):
        from trips.models import Review
        qs  = Review.objects.filter(agency=self, is_visible=True)
        agg = qs.aggregate(
            avg_overall=Avg("overall_rating"),
            avg_guides=Avg("rating_guides"),
            avg_accommodation=Avg("rating_accommodation"),
            avg_value=Avg("rating_value"),
            avg_transport=Avg("rating_transport"),
            total=Count("id"),
        )
        type(self).objects.filter(pk=self.pk).update(
            avg_overall_rating=round(agg["avg_overall"] or 0, 2),
            avg_guides_rating=agg["avg_guides"],
            avg_accommodation_rating=agg["avg_accommodation"],
            avg_value_rating=agg["avg_value"],
            avg_transport_rating=agg["avg_transport"],
            total_review_count=agg["total"] or 0,
        )
 
    def __str__(self):
        return self.name
 
    def is_approved(self):
        return self.status == 'approved'
 
    class Meta:
        verbose_name_plural = "Agencies"
 
 
class Package(models.Model):
    CATEGORY_CHOICES = [
        ('adventure', '🏔️ Adventure'),
        ('cultural',  '🏛️ Cultural'),
        ('leisure',   '🌴 Leisure'),
        ('romantic',  '💑 Romantic'),
        ('family',    '👨‍👩‍👧 Family'),
        ('luxury',    '✨ Luxury'),
        ('solo',      '🎒 Solo'),
    ]
    agency      = models.ForeignKey(Agency, on_delete=models.CASCADE, related_name='packages')
    title       = models.CharField(max_length=255)
    destination = models.CharField(max_length=255)
    description = models.TextField()
    duration    = models.IntegerField(help_text="Number of days")
    price       = models.IntegerField(help_text="Price in INR")
    category    = models.CharField(max_length=50, choices=CATEGORY_CHOICES)
    inclusions  = models.TextField(help_text="What's included", blank=True)
    image_url   = models.URLField(blank=True)
    is_active   = models.BooleanField(default=True)
    created_at  = models.DateTimeField(auto_now_add=True)
 
    def __str__(self):
        return f"{self.title} — {self.agency.name}"
 
    class Meta:
        verbose_name_plural = "Packages"
 
 
class PackageImage(models.Model):
    package    = models.ForeignKey(Package, on_delete=models.CASCADE, related_name='images')
    image_url  = models.URLField(blank=True)
    image_file = models.ImageField(upload_to='packages/', blank=True, null=True)
    caption    = models.CharField(max_length=100, blank=True)
    order      = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
 
    class Meta:
        ordering = ['order', 'created_at']
 
    def get_url(self):
        if self.image_file:
            return self.image_file.url
        return self.image_url
 
    def __str__(self):
        return f"Image for {self.package.title}"
 
 
class PackageReview(models.Model):
    package    = models.ForeignKey(Package, on_delete=models.CASCADE, related_name='reviews')
    user       = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    booking    = models.OneToOneField('Booking', on_delete=models.CASCADE, related_name='package_review', null=True, blank=True)
    rating     = models.PositiveSmallIntegerField(default=5)
    title      = models.CharField(max_length=120, blank=True)
    body       = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
 
    class Meta:
        ordering        = ['-created_at']
        unique_together = ['package', 'user']
 
    def __str__(self):
        return f"{self.user.username} → {self.package.title} ({self.rating}★)"
 
 
class Offer(models.Model):
    agency     = models.ForeignKey(Agency, on_delete=models.CASCADE)
    trip       = models.ForeignKey("trips.Trip", on_delete=models.CASCADE)
    package    = models.ForeignKey(Package, on_delete=models.SET_NULL, null=True, blank=True)
    price      = models.IntegerField()
    message    = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
 
    def __str__(self):
        return f"{self.agency.name} → {self.trip}"
 
    class Meta:
        verbose_name_plural = "Offers"
 
 
class ChatRoom(models.Model):
    offer      = models.OneToOneField(Offer, on_delete=models.CASCADE, related_name='chatroom', null=True, blank=True)
    package    = models.ForeignKey('Package', on_delete=models.SET_NULL, null=True, blank=True, related_name='chatrooms')
    user       = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='chatrooms')
    agency     = models.ForeignKey(Agency, on_delete=models.CASCADE, related_name='chatrooms')
    is_active  = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True, null=True)
 
    def __str__(self):
        if self.package:
            return f"PackageChat: {self.user.username} ↔ {self.agency.name} ({self.package.title})"
        return f"Chat: {self.user.username} ↔ {self.agency.name}"
 
 
class Message(models.Model):
    SENDER_TYPES = [('user', 'User'), ('agency', 'Agency'), ('system', 'System')]
 
    room               = models.ForeignKey(ChatRoom, on_delete=models.CASCADE, related_name='messages')
    sender_type        = models.CharField(max_length=10, choices=SENDER_TYPES)
    content            = models.TextField()
    is_payment_request = models.BooleanField(default=False)
    is_read            = models.BooleanField(default=False)
    created_at         = models.DateTimeField(auto_now_add=True, null=True)
 
    class Meta:
        ordering = ['created_at']
 
    def __str__(self):
        return f"{self.sender_type}: {self.content[:40]}"
 
 
class PaymentRequest(models.Model):
    STATUS_CHOICES = [
        ('pending',  'Pending'),
        ('accepted', 'Accepted'),
        ('rejected', 'Rejected'),
        ('paid',     'Paid'),
    ]
    room                = models.ForeignKey(ChatRoom, on_delete=models.CASCADE, related_name='payment_requests')
    message             = models.OneToOneField(Message, on_delete=models.CASCADE, related_name='payment_request')
    amount              = models.IntegerField()
    note                = models.TextField(blank=True)
    status              = models.CharField(max_length=10, choices=STATUS_CHOICES, default='pending')
    razorpay_order_id   = models.CharField(max_length=100, blank=True, null=True)
    razorpay_payment_id = models.CharField(max_length=100, blank=True, null=True)
    created_at          = models.DateTimeField(auto_now_add=True, null=True)
 
    def __str__(self):
        return f"PaymentRequest ₹{self.amount} — {self.status}"
 
 
class Booking(models.Model):
    STATUS_CHOICES = [
        ('pending',   'Pending'),
        ('confirmed', 'Confirmed'),
        ('ongoing',   'Ongoing'),
        ('completed', 'Completed'),
        ('cancelled', 'Cancelled'),
    ]
    user                = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    offer               = models.ForeignKey('Offer', on_delete=models.SET_NULL, null=True, blank=True)
    package             = models.ForeignKey('Package', on_delete=models.SET_NULL, null=True, blank=True)
    base_amount         = models.IntegerField(default=0)
    gst_amount          = models.IntegerField(default=0)
    commission_amount   = models.IntegerField(default=0)
    agency_payout       = models.IntegerField(default=0)
    platform_fee        = models.IntegerField(default=0)
    total_amount        = models.IntegerField(default=0)
    is_paid             = models.BooleanField(default=False)
    status              = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    razorpay_order_id   = models.CharField(max_length=100, blank=True, null=True)
    razorpay_payment_id = models.CharField(max_length=100, blank=True, null=True)
    created_at          = models.DateTimeField(auto_now_add=True, null=True)
 
    def __str__(self):
        return f"Booking #{self.id} — {'Paid' if self.is_paid else 'Pending'}"
 
    @property
    def agency(self):
        if self.offer:
            return self.offer.agency
        if self.package:
            return self.package.agency
        return None
 
    @property
    def traveller(self):
        return self.user
 
    class Meta:
        verbose_name_plural = "Bookings"
 
 
class AgencyWarning(models.Model):
    REASON_CHOICES = [
        ('contact_sharing',   'Attempted contact sharing'),
        ('platform_redirect', 'Attempted platform redirect'),
    ]
    agency          = models.ForeignKey(Agency, on_delete=models.CASCADE, related_name='warnings')
    room            = models.ForeignKey(ChatRoom, on_delete=models.CASCADE, related_name='warnings')
    reason          = models.CharField(max_length=30, choices=REASON_CHOICES)
    flagged_content = models.TextField()
    created_at      = models.DateTimeField(auto_now_add=True)
 
    def __str__(self):
        return f"Warning #{self.pk} — {self.agency.name} ({self.reason})"
 
 
class TripStatus(models.Model):
    STATUS_CHOICES = [
        ('confirmed',  '✅ Booking Confirmed'),
        ('preparing',  '📋 Preparing Itinerary'),
        ('documents',  '📄 Documents Ready'),
        ('departing',  '🚀 Departing Soon'),
        ('on_trip',    '🗺️ Currently On Trip'),
        ('returning',  '🏠 Returning Home'),
        ('completed',  '🎉 Trip Completed'),
        ('cancelled',  '❌ Cancelled'),
    ]
    booking    = models.OneToOneField('Booking', on_delete=models.CASCADE, related_name='trip_status')
    status     = models.CharField(max_length=20, choices=STATUS_CHOICES, default='confirmed')
    note       = models.TextField(blank=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)
 
    def __str__(self):
        return f"TripStatus #{self.pk} — {self.status}"
 
 
class TripUpdate(models.Model):
    trip_status = models.ForeignKey(TripStatus, on_delete=models.CASCADE, related_name='updates')
    status      = models.CharField(max_length=20, choices=TripStatus.STATUS_CHOICES)
    note        = models.TextField(blank=True)
    created_at  = models.DateTimeField(auto_now_add=True)
 
    class Meta:
        ordering = ['-created_at']
 
    def __str__(self):
        return f"{self.trip_status} — {self.status}"
 
 
class PackageView(models.Model):
    package    = models.ForeignKey(Package, on_delete=models.CASCADE, related_name='views')
    user       = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
 
    class Meta:
        verbose_name_plural = "Package Views"
 
    def __str__(self):
        return f"View — {self.package.title}"
 
 
# ── Phase: AI Support & Refunds ──────────────────────────────────────────────
 
class SupportTicket(models.Model):
    STATUS_CHOICES = [
        ('open',       'Open — AI Handling'),
        ('escalated',  'Escalated to Team'),
        ('in_review',  'In Review'),
        ('resolved',   'Resolved'),
        ('closed',     'Closed'),
    ]
    CATEGORY_CHOICES = [
        ('payment',    'Payment Issue'),
        ('refund',     'Refund Request'),
        ('booking',    'Booking Problem'),
        ('agency',     'Agency Complaint'),
        ('technical',  'Technical Issue'),
        ('other',      'Other'),
    ]
    user            = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='support_tickets')
    booking         = models.ForeignKey(Booking, on_delete=models.SET_NULL, null=True, blank=True, related_name='support_tickets')
    subject         = models.CharField(max_length=255)
    category        = models.CharField(max_length=20, choices=CATEGORY_CHOICES, default='other')
    status          = models.CharField(max_length=20, choices=STATUS_CHOICES, default='open')
    is_escalated    = models.BooleanField(default=False)
    escalated_at    = models.DateTimeField(null=True, blank=True)
    assigned_to     = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='assigned_tickets')
    resolved_at     = models.DateTimeField(null=True, blank=True)
    created_at      = models.DateTimeField(auto_now_add=True)
    updated_at      = models.DateTimeField(auto_now=True)
 
    class Meta:
        ordering = ['-created_at']
 
    def __str__(self):
        return f"Ticket #{self.pk} — {self.subject} [{self.status}]"
 
 
class SupportMessage(models.Model):
    SENDER_TYPES = [
        ('user',   'User'),
        ('ai',     'AI Assistant'),
        ('agent',  'Support Agent'),
        ('system', 'System'),
    ]
    ticket     = models.ForeignKey(SupportTicket, on_delete=models.CASCADE, related_name='messages')
    sender     = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    sender_type = models.CharField(max_length=10, choices=SENDER_TYPES)
    content    = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
 
    class Meta:
        ordering = ['created_at']
 
    def __str__(self):
        return f"[{self.sender_type}] {self.content[:60]}"
 
 
class RefundRequest(models.Model):
    STATUS_CHOICES = [
        ('pending',   'Pending Review'),
        ('approved',  'Approved'),
        ('processed', 'Processed — Refund Sent'),
        ('rejected',  'Rejected'),
    ]
    REASON_CHOICES = [
        ('agency_cancelled',  'Agency Cancelled Trip'),
        ('traveller_cancel',  'Traveller Cancellation'),
        ('service_failure',   'Service Not Delivered'),
        ('double_charge',     'Double Charged'),
        ('fraud',             'Fraudulent Transaction'),
        ('other',             'Other'),
    ]
    ticket              = models.OneToOneField(SupportTicket, on_delete=models.CASCADE, related_name='refund_request', null=True, blank=True)
    booking             = models.ForeignKey(Booking, on_delete=models.CASCADE, related_name='refund_requests')
    requested_by        = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='refund_requests')
    reason              = models.CharField(max_length=30, choices=REASON_CHOICES)
    description         = models.TextField(blank=True)
    amount              = models.IntegerField(help_text="Amount to refund in INR")
    status              = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    razorpay_refund_id  = models.CharField(max_length=100, blank=True, null=True)
    processed_by        = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='processed_refunds')
    processed_at        = models.DateTimeField(null=True, blank=True)
    rejection_reason    = models.TextField(blank=True)
    created_at          = models.DateTimeField(auto_now_add=True)
    updated_at          = models.DateTimeField(auto_now=True)
 
    class Meta:
        ordering = ['-created_at']
 
    def __str__(self):
        return f"Refund #{self.pk} — ₹{self.amount} [{self.status}]"
 
    
class Coupon(models.Model):
    DISCOUNT_TYPE_CHOICES = [
        ('percentage', 'Percentage Off'),
    ]
    STATUS_CHOICES = [
        ('active',   'Active'),
        ('inactive', 'Inactive'),
        ('expired',  'Expired'),
    ]
 
    code              = models.CharField(max_length=20, unique=True, db_index=True)
    description       = models.CharField(max_length=255, blank=True)
    discount_type     = models.CharField(max_length=15, choices=DISCOUNT_TYPE_CHOICES, default='percentage')
    discount_value    = models.DecimalField(max_digits=5, decimal_places=2, help_text="Percentage value e.g. 10 for 10%")
    max_discount_cap  = models.IntegerField(null=True, blank=True, help_text="Max discount in ₹ (e.g. cap 10% at ₹500)")
    min_booking_amount= models.IntegerField(default=0, help_text="Minimum booking amount to apply coupon")
    max_uses          = models.IntegerField(null=True, blank=True, help_text="Leave blank for unlimited")
    used_count        = models.IntegerField(default=0, editable=False)
    valid_from        = models.DateTimeField()
    valid_until       = models.DateTimeField(null=True, blank=True, help_text="Leave blank for no expiry")
    status            = models.CharField(max_length=10, choices=STATUS_CHOICES, default='active')
 
    # Who created it
    created_by_admin  = models.BooleanField(default=False)
    created_by_agency = models.ForeignKey(
        'Agency', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='coupons'
    )
 
    # Which agency this coupon is valid for (null = valid for all)
    applicable_agency = models.ForeignKey(
        'Agency', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='applicable_coupons'
    )
 
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
 
    class Meta:
        ordering = ['-created_at']
 
    def __str__(self):
        return f"{self.code} — {self.discount_value}% off"
 
    def is_valid(self):
        from django.utils import timezone
        now = timezone.now()
        if self.status != 'active':
            return False, "This coupon is no longer active."
        if now < self.valid_from:
            return False, "This coupon is not valid yet."
        if self.valid_until and now > self.valid_until:
            self.status = 'expired'
            self.save(update_fields=['status'])
            return False, "This coupon has expired."
        if self.max_uses and self.used_count >= self.max_uses:
            return False, "This coupon has reached its usage limit."
        return True, "Valid"
 
    def calculate_discount(self, base_amount):
        """
        Returns (discount_amount, final_amount) in INR integers.
        Trivasta always takes 5% of ORIGINAL base_amount.
        Discount comes from agency share — Trivasta never loses commission.
        """
        discount = int((self.discount_value / 100) * base_amount)
        if self.max_discount_cap:
            discount = min(discount, self.max_discount_cap)
        final = base_amount - discount
        return discount, final
 
    def mark_used(self):
        Coupon.objects.filter(pk=self.pk).update(used_count=models.F('used_count') + 1)
 
 
class CouponUsage(models.Model):
    """Tracks which user used which coupon on which booking."""
    coupon     = models.ForeignKey(Coupon, on_delete=models.CASCADE, related_name='usages')
    user       = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    booking    = models.OneToOneField('Booking', on_delete=models.CASCADE, related_name='coupon_usage')
    discount_applied = models.IntegerField(help_text="Actual discount amount in ₹")
    created_at = models.DateTimeField(auto_now_add=True)
 
    class Meta:
        unique_together = ['coupon', 'user']  # one coupon per user
 
    def __str__(self):
        return f"{self.user.username} used {self.coupon.code} — saved ₹{self.discount_applied}"
 
 
class AgencyBankDetails(models.Model):
    """
    Stores agency bank account for Razorpay Route payouts.
    Created when agency registers. KYC verified by Trivasta admin.
    """
    KYC_STATUS_CHOICES = [
        ('pending',  'Pending Submission'),
        ('submitted','Submitted — Under Review'),
        ('verified', 'Verified'),
        ('rejected', 'Rejected'),
    ]
    ACCOUNT_TYPE_CHOICES = [
        ('savings', 'Savings'),
        ('current', 'Current'),
    ]
 
    agency = models.OneToOneField(
        'Agency', on_delete=models.CASCADE, related_name='bank_details'
    )
 
    # Bank account
    account_holder_name = models.CharField(max_length=255)
    account_number      = models.CharField(max_length=20)
    ifsc_code           = models.CharField(max_length=11)
    account_type        = models.CharField(max_length=10, choices=ACCOUNT_TYPE_CHOICES, default='current')
    bank_name           = models.CharField(max_length=100, blank=True)
 
    # GST & PAN
    pan_number          = models.CharField(max_length=10, blank=True)
    gst_number          = models.CharField(max_length=15, blank=True)
 
    # Razorpay Route linked account
    razorpay_account_id = models.CharField(max_length=100, blank=True, null=True, help_text="Razorpay linked account ID")
    razorpay_fund_account_id = models.CharField(max_length=100, blank=True, null=True)
 
    # KYC
    kyc_status          = models.CharField(max_length=15, choices=KYC_STATUS_CHOICES, default='pending')
    kyc_rejection_reason= models.TextField(blank=True)
    kyc_verified_at     = models.DateTimeField(null=True, blank=True)
    kyc_verified_by     = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='kyc_verifications'
    )
 
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
 
    def __str__(self):
        return f"{self.agency.name} — {self.account_holder_name} ({self.kyc_status})"
 
    @property
    def is_payout_ready(self):
        """True only if KYC verified and Razorpay linked account exists."""
        return self.kyc_status == 'verified' and bool(self.razorpay_account_id)
 
 
class PayoutRecord(models.Model):
    """
    Records every payout split made via Razorpay Route.
    Created automatically when a booking payment is confirmed.
    """
    STATUS_CHOICES = [
        ('pending',    'Pending'),
        ('processing', 'Processing'),
        ('paid',       'Paid'),
        ('failed',     'Failed'),
    ]
 
    booking              = models.OneToOneField('Booking', on_delete=models.CASCADE, related_name='payout_record')
    agency               = models.ForeignKey('Agency', on_delete=models.CASCADE, related_name='payouts')
 
    # Amounts
    total_amount         = models.IntegerField(help_text="Total amount paid by traveller")
    base_amount          = models.IntegerField(help_text="Base before GST")
    gst_amount           = models.IntegerField(help_text="GST collected")
    discount_amount      = models.IntegerField(default=0, help_text="Coupon discount applied")
    trivasta_commission  = models.IntegerField(help_text="5% of original base — Trivasta earnings")
    agency_payout_amount = models.IntegerField(help_text="Amount to transfer to agency")
 
    # Razorpay Route transfer details
    razorpay_transfer_id = models.CharField(max_length=100, blank=True, null=True)
    status               = models.CharField(max_length=15, choices=STATUS_CHOICES, default='pending')
    failure_reason       = models.TextField(blank=True)
 
    created_at  = models.DateTimeField(auto_now_add=True)
    paid_at     = models.DateTimeField(null=True, blank=True)
 
    class Meta:
        ordering = ['-created_at']
 
    def __str__(self):
        return f"Payout #{self.pk} — {self.agency.name} ₹{self.agency_payout_amount} ({self.status})"

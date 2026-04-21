from django.db import models
from django.conf import settings
from marketplace.models import Booking  
from django.contrib.auth.models import User


class ContactMessage(models.Model):
    SUBJECT_CHOICES = [
        ('general',     'General Enquiry'),
        ('booking',     'Booking Issue'),
        ('refund',      'Refund Request'),
        ('agency',      'Agency Complaint'),
        ('payment',     'Payment Problem'),
        ('technical',   'Technical Issue'),
        ('partnership', 'Partnership / B2B'),
        ('press',       'Press / Media'),
        ('other',       'Other'),
    ]
    first_name = models.CharField(max_length=100)
    last_name  = models.CharField(max_length=100, blank=True)
    email      = models.EmailField()
    subject    = models.CharField(max_length=30, choices=SUBJECT_CHOICES, default='other')
    message    = models.TextField()
    booking    = models.ForeignKey(
        'marketplace.Booking', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='contact_messages'
    )
    user       = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='contact_messages'
    )
    is_read    = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.first_name} {self.last_name} — {self.get_subject_display()}"


class Profile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    is_premium = models.BooleanField(default=False)

    def __str__(self):
        return f"{self.user.username} Profile"
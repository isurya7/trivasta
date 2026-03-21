from django.db import models
from django.conf import settings
from django.core.validators import MinValueValidator, MaxValueValidator
from django.core.exceptions import ValidationError


class Trip(models.Model):
    TRAVEL_MODES = [
        ('any',    '🚀 Any / Flexible'),
        ('flight', '✈️ Flight'),
        ('train',  '🚂 Train'),
        ('bus',    '🚌 Bus'),
        ('car',    '🚗 Self Drive'),
        ('cruise', '🚢 Cruise'),
    ]
    user        = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    destination = models.CharField(max_length=255)
    origin      = models.CharField(max_length=255, blank=True, default='')
    budget      = models.IntegerField()
    budget_type = models.CharField(max_length=20, default='total',
                                   choices=[('total', 'Total Budget'), ('per_person', 'Per Person')])
    days        = models.IntegerField()
    start_date  = models.DateField(null=True, blank=True)
    end_date    = models.DateField(null=True, blank=True)
    travel_type = models.CharField(max_length=100)
    travel_mode = models.CharField(max_length=20, choices=TRAVEL_MODES, default='any')
    num_people  = models.IntegerField(default=1)
    created_at  = models.DateTimeField(auto_now_add=True)

    def total_budget(self):
        if self.budget_type == 'per_person':
            return self.budget * self.num_people
        return self.budget

    def __str__(self):
        return f"{self.destination} ({self.days} days)"

    class Meta:
        verbose_name_plural = "Trips"


class Itinerary(models.Model):
    trip           = models.OneToOneField(Trip, on_delete=models.CASCADE)
    content        = models.TextField()
    estimated_cost = models.IntegerField()
    created_at     = models.DateTimeField(auto_now_add=True, null=True)

    class Meta:
        verbose_name_plural = "Itineraries"


# ── Reviews — tied to marketplace.Booking ────────────────────────────────────

class Review(models.Model):
    # Points to marketplace.Booking (the real booking with payment)
    booking  = models.OneToOneField("marketplace.Booking", on_delete=models.CASCADE, related_name="review")
    reviewer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="reviews_given")
    agency   = models.ForeignKey("marketplace.Agency", on_delete=models.CASCADE, related_name="reviews_received")

    overall_rating       = models.PositiveSmallIntegerField(validators=[MinValueValidator(1), MaxValueValidator(5)])
    rating_guides        = models.PositiveSmallIntegerField(null=True, blank=True, validators=[MinValueValidator(1), MaxValueValidator(5)])
    rating_accommodation = models.PositiveSmallIntegerField(null=True, blank=True, validators=[MinValueValidator(1), MaxValueValidator(5)])
    rating_value         = models.PositiveSmallIntegerField(null=True, blank=True, validators=[MinValueValidator(1), MaxValueValidator(5)])
    rating_transport     = models.PositiveSmallIntegerField(null=True, blank=True, validators=[MinValueValidator(1), MaxValueValidator(5)])

    title         = models.CharField(max_length=120)
    body          = models.TextField(max_length=2000)
    is_visible    = models.BooleanField(default=True)
    helpful_votes = models.PositiveIntegerField(default=0)
    created_at    = models.DateTimeField(auto_now_add=True)
    updated_at    = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Review"
        verbose_name_plural = "Reviews"

    def __str__(self):
        return f"Review by {self.reviewer} for {self.agency} — {self.overall_rating}★"

    def clean(self):
        # booking.status uses marketplace.Booking.STATUS_CHOICES
        if self.booking.status != "completed":
            raise ValidationError("Reviews can only be submitted after the trip has been marked as completed.")
        if self.booking.user != self.reviewer:
            raise ValidationError("You can only review your own bookings.")

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)
        self.agency.update_rating_cache()


class ReviewReply(models.Model):
    review     = models.OneToOneField(Review, on_delete=models.CASCADE, related_name="agency_reply")
    replied_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="review_replies")
    body       = models.TextField(max_length=1000)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Review Reply"
        verbose_name_plural = "Review Replies"

    def __str__(self):
        return f"Reply to review #{self.review_id} by {self.replied_by}"

    def clean(self):
        # request.user.agency check done in view; model-level guard:
        try:
            if self.replied_by.agency != self.review.agency:
                raise ValidationError("Only the agency can reply to its own reviews.")
        except Exception:
            raise ValidationError("Only the agency can reply to its own reviews.")

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class ReviewHelpfulVote(models.Model):
    review     = models.ForeignKey(Review, on_delete=models.CASCADE, related_name="helpful_vote_records")
    voter      = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="helpful_votes_cast")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("review", "voter")

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        new_count = self.review.helpful_vote_records.count()
        Review.objects.filter(pk=self.review_id).update(helpful_votes=new_count)
import logging

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.db import IntegrityError

from .forms import TripForm
from .services.ai import generate_itinerary, generate_fallback_itinerary, AIQuotaExceeded, AIGenerationFailed
from .services.itinerary_key import build_cache_key
from .validation import MIN_PER_PERSON, MIN_GROUP_TOTAL
from .models import Trip, Itinerary, Review, ReviewReply, ReviewHelpfulVote
from marketplace.models import Offer, Booking, Package   # ← Booking from marketplace

logger = logging.getLogger(__name__)


def home(request):
    packages = Package.objects.filter(
        is_active=True, agency__status='approved'
    ).select_related('agency').order_by('-created_at')[:12]

    return render(request, "home.html", {
        "page_title": "Trivasta — AI-Powered Travel Planning",
        "packages":   packages,
    })


# ── Planner ───────────────────────────────────────────────────────────────────

# Which wizard step (1-4) each form field lives on, so a server-side error
# reopens the step the user needs to fix.
FIELD_STEP = {
    "origin": 1, "destination": 1, "travel_type": 1,
    "start_date": 2, "end_date": 2, "days": 2, "num_people": 2,
    "travel_mode": 3, "additional_preferences": 3,
    "budget": 4, "budget_type": 4,
}


def _first_error_step(form):
    steps = [FIELD_STEP.get(field, 4) for field in form.errors]
    return min(steps) if steps else 1


def _render_planner(request, form):
    data = form.data if form.is_bound else {}
    return render(request, "trips/planner.html", {
        "form": form,
        "page_title": "Plan Your Trip",
        # Selects/radios are re-applied by JS from this after a failed POST.
        "form_state": {k: data.get(k, "") for k in ("travel_type", "travel_mode", "budget_type")},
        "error_step": _first_error_step(form),
        "min_per_person": MIN_PER_PERSON,
        "min_group_total": MIN_GROUP_TOTAL,
    })


def _log_cache_key(trip):
    """Logging only (no cache yet): lets us measure how often trips repeat."""
    try:
        logger.info("itinerary_cache_key=%s", build_cache_key(trip))
    except Exception:
        logger.exception("Could not build itinerary cache key")


@login_required
def planner(request):
    if request.method != "POST":
        return _render_planner(request, TripForm())

    form = TripForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Please correct the errors below.")
        return _render_planner(request, form)

    trip = form.save(commit=False)
    trip.user = request.user
    trip.save()
    _log_cache_key(trip)

    fallback_kwargs = dict(
        destination=trip.destination, days=trip.days, budget=trip.budget,
        num_people=trip.num_people, travel_type=trip.travel_type,
        travel_mode=trip.travel_mode, origin=trip.origin,
        budget_type=trip.budget_type,
    )

    try:
        itinerary_content = generate_itinerary(
            destination=trip.destination, days=trip.days,
            budget=trip.budget, travel_type=trip.travel_type,
            travel_mode=trip.travel_mode, origin=trip.origin,
            num_people=trip.num_people, budget_type=trip.budget_type,
            start_date=str(trip.start_date) if trip.start_date else "",
            additional_preferences=trip.additional_preferences,
        )
    except AIQuotaExceeded:
        messages.warning(request, "Our AI planner hit its usage limit — showing a starter itinerary instead.")
        itinerary_content = generate_fallback_itinerary(**fallback_kwargs)
    except AIGenerationFailed:
        messages.warning(request, "Couldn't generate a full AI itinerary — showing a starter plan instead.")
        itinerary_content = generate_fallback_itinerary(**fallback_kwargs)

    Itinerary.objects.create(
        trip=trip, content=itinerary_content,
        estimated_cost=trip.total_budget(),
    )
    messages.success(request, "✅ Your trip has been planned!")
    return redirect("trip_detail", pk=trip.id)


@login_required
def compare_offers(request, trip_id):
    trip   = get_object_or_404(Trip, pk=trip_id, user=request.user)
    offers = Offer.objects.filter(trip=trip).select_related('agency').order_by('price')
    return render(request, 'marketplace/compare_offers.html', {'trip': trip, 'offers': offers})


@login_required
def trip_detail(request, pk):
    trip      = get_object_or_404(Trip, pk=pk, user=request.user)
    itinerary = Itinerary.objects.filter(trip=trip).first()
    return render(request, "trips/trip_detail.html", {
        "trip": trip, "itinerary": itinerary,
        "destination": trip.destination, "budget": trip.budget,
        "duration": trip.days, "travel_type": trip.travel_type,
        "created_at": trip.created_at,
    })


def about(request):   return render(request, "about.html",          {"page_title": "About Us"})
def contact(request): return render(request, "contact.html",        {"page_title": "Contact Us"})
def privacy(request): return render(request, "privacy_policy.html", {"page_title": "Privacy Policy"})
def terms(request):   return render(request, "terms.html",          {"page_title": "Terms & Conditions"})


# ── Reviews ───────────────────────────────────────────────────────────────────

@login_required
def submit_review(request, booking_id):
    # Use marketplace.Booking (the real paid booking)
    booking = get_object_or_404(Booking, pk=booking_id, user=request.user)

    if booking.status != "completed":
        messages.error(request, "You can only review a trip after it has been completed.")
        return redirect("dashboard")

    if hasattr(booking, "review"):
        messages.info(request, "You have already submitted a review for this trip.")
        return redirect("dashboard")

    if request.method == "POST":
        overall = request.POST.get("overall_rating")
        title   = request.POST.get("title", "").strip()
        body    = request.POST.get("body", "").strip()

        errors = []
        if not overall or not overall.isdigit() or not (1 <= int(overall) <= 5):
            errors.append("Please select an overall rating between 1 and 5.")
        if not title:
            errors.append("Please enter a review title.")
        if len(body) < 20:
            errors.append("Your review must be at least 20 characters.")

        if errors:
            for e in errors:
                messages.error(request, e)
            return render(request, "trips/submit_review.html", {"booking": booking, "post_data": request.POST})

        review = Review(
            booking=booking,
            reviewer=request.user,
            agency=booking.agency,
            overall_rating=int(overall),
            title=title,
            body=body,
        )

        for field in ("rating_guides", "rating_accommodation", "rating_value", "rating_transport"):
            val = request.POST.get(field)
            if val and val.isdigit() and 1 <= int(val) <= 5:
                setattr(review, field, int(val))

        try:
            review.save()
            messages.success(request, "Your review has been published. Thank you!")
            return redirect("dashboard")
        except Exception as e:
            messages.error(request, f"Could not save review: {e}")

    return render(request, "trips/submit_review.html", {"booking": booking})


def agency_reviews(request, agency_id):
    from marketplace.models import Agency
    agency = get_object_or_404(Agency, pk=agency_id)

    reviews = (
        Review.objects.filter(agency=agency, is_visible=True)
        .select_related("reviewer", "agency_reply", "agency_reply__replied_by")
        .order_by("-created_at")
    )

    star_filter = request.GET.get("stars")
    if star_filter and star_filter.isdigit():
        reviews = reviews.filter(overall_rating=int(star_filter))

    sort     = request.GET.get("sort", "newest")
    sort_map = {
        "newest": "-created_at", "oldest": "created_at",
        "highest": "-overall_rating", "lowest": "overall_rating",
        "helpful": "-helpful_votes",
    }
    reviews  = reviews.order_by(sort_map.get(sort, "-created_at"))

    all_reviews = Review.objects.filter(agency=agency, is_visible=True)
    star_dist   = {i: all_reviews.filter(overall_rating=i).count() for i in range(5, 0, -1)}

    return render(request, "trips/agency_reviews.html", {
        "agency": agency, "reviews": reviews,
        "star_dist": star_dist, "current_sort": sort, "current_star": star_filter,
    })


@login_required
@require_POST
def reply_to_review(request, review_id):
    review = get_object_or_404(Review, pk=review_id)

    if not hasattr(request.user, 'agency') or request.user.agency != review.agency:
        messages.error(request, "Only the agency can reply to this review.")
        return redirect("agency_reviews", agency_id=review.agency_id)

    if hasattr(review, "agency_reply"):
        messages.error(request, "You have already replied to this review.")
        return redirect("agency_reviews", agency_id=review.agency_id)

    body = request.POST.get("body", "").strip()
    if len(body) < 10:
        messages.error(request, "Reply must be at least 10 characters.")
        return redirect("agency_reviews", agency_id=review.agency_id)

    ReviewReply.objects.create(review=review, replied_by=request.user, body=body)
    messages.success(request, "Your reply has been posted.")
    return redirect("agency_reviews", agency_id=review.agency_id)


@login_required
@require_POST
def mark_helpful(request, review_id):
    review = get_object_or_404(Review, pk=review_id)

    if review.reviewer == request.user:
        return JsonResponse({"error": "You cannot vote on your own review."}, status=400)

    try:
        ReviewHelpfulVote.objects.create(review=review, voter=request.user)
        return JsonResponse({"helpful_votes": review.helpful_vote_records.count()})
    except IntegrityError:
        ReviewHelpfulVote.objects.filter(review=review, voter=request.user).delete()
        new_count = review.helpful_vote_records.count()
        Review.objects.filter(pk=review_id).update(helpful_votes=new_count)
        return JsonResponse({"helpful_votes": new_count, "removed": True})
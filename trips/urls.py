from django.urls import path
from . import views
from .views import submit_review,agency_reviews,reply_to_review,mark_helpful


urlpatterns = [
    path('planner/',      views.planner,     name='planner'),
    path('<int:pk>/',     views.trip_detail, name='trip_detail'),
    path('trips/<int:trip_id>/compare/', views.compare_offers, name='compare_offers'),

    #REVIEWS
    path("bookings/<int:booking_id>/review/",  submit_review,    name="submit_review"),
    path("agencies/<int:agency_id>/reviews/",  agency_reviews,   name="agency_reviews"),
    path("reviews/<int:review_id>/reply/",     reply_to_review,  name="reply_to_review"),
    path("reviews/<int:review_id>/helpful/",   mark_helpful,     name="mark_helpful"),
]
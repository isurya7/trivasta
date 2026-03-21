from django.urls import path
from . import views

urlpatterns = [
    # ── Packages ──────────────────────────────────────────────────
    path('packages/',                                  views.package_search,               name='package_search'),
    path('packages/<int:pk>/',                         views.package_detail,               name='package_detail'),
    path('packages/<int:pk>/chat/',                    views.package_chat,                 name='package_chat'),
    path('packages/<int:pk>/book/',                    views.package_book,                 name='package_book'),
    path('packages/<int:pk>/book/success/',            views.package_book_success,         name='package_book_success'),
    path('packages/booking/<int:booking_id>/',         views.package_booking_confirmation, name='package_booking_confirmation'),
    path('packages/<int:pk>/toggle/',                  views.package_toggle,               name='package_toggle'),

    # ── Offers ───────────────────────────────────────────────────
    path('offers/<int:trip_id>/',                      views.offers,                       name='offers'),
    path('offer/<int:offer_id>/chat/',                 views.open_chat,                    name='open_chat'),

    # ── Checkout / payments ───────────────────────────────────────
    path('checkout/<int:offer_id>/',                   views.checkout,                     name='checkout'),
    path('payment/success/',                           views.payment_success,              name='payment_success'),
    path('payment/failed/',                            views.payment_failed,               name='payment_failed'),

    # ── Bookings ──────────────────────────────────────────────────
    path('booking/<int:booking_id>/',                  views.booking_confirmation,         name='booking_confirmation'),
    path('booking/<int:booking_id>/track/',            views.trip_tracking,                name='trip_tracking'),
    path('booking/<int:booking_id>/update-status/',    views.update_trip_status,           name='update_trip_status'),
    path('booking/<int:booking_id>/refund/',           views.request_refund,               name='request_refund'),

    # ── Agency auth ───────────────────────────────────────────────
    path('agency/register/',                           views.agency_register,              name='agency_register'),
    path('agency/login/',                              views.agency_login,                 name='agency_login'),
    path('agency/logout/',                             views.agency_logout,                name='agency_logout'),
    path('agency/subscribe/',                          views.agency_subscribe,             name='agency_subscribe'),
    path('agency/payment/success/',                    views.agency_payment_success,       name='agency_payment_success'),
    path('agency/payment/failed/',                     views.agency_payment_failed,        name='agency_payment_failed'),

    # ── Agency dashboard ──────────────────────────────────────────
    path('agency/dashboard/',                          views.agency_dashboard,             name='agency_dashboard'),
    path('agency/profile/',                            views.agency_profile,               name='agency_profile'),
    path('agency/profile/edit/',                       views.agency_profile_edit,          name='agency_profile_edit'),
    path('agency/packages/create/',                    views.package_create,               name='package_create'),
    path('agency/packages/<int:pk>/edit/',             views.package_edit,                 name='package_edit'),
    path('agency/packages/<int:pk>/delete/',           views.package_delete,               name='package_delete'),
    path('agency/trips/<int:trip_id>/offer/',          views.send_offer,                   name='send_offer'),

    # ── Chat ──────────────────────────────────────────────────────
    path('chat/<int:room_id>/',                        views.chat_room,                    name='chat_room'),
    path('chat/<int:room_id>/send/',                   views.send_message,                 name='send_message'),
    path('chat/<int:room_id>/raise-payment/',          views.raise_payment_request,        name='raise_payment_request'),
    path('chat/payment/<int:pr_id>/accept/',           views.accept_payment_request,       name='accept_payment_request'),
    path('chat/payment/<int:pr_id>/reject/',           views.reject_payment_request,       name='reject_payment_request'),
    path('chat/payment/success/',                      views.chat_payment_success,         name='chat_payment_success'),

    # ── Support ───────────────────────────────────────────────────
    path('support/',                                   views.support_chat,                 name='support_chat'),
]
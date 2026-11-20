from django.urls import path
from . import views

urlpatterns = [
    # Package payments
    path('packages/<int:pk>/book/',            views.package_book,            name='package_book'),
    path('packages/<int:pk>/book/success/',    views.package_book_success,    name='package_book_success'),

    # Offer checkout
    path('checkout/<int:offer_id>/',           views.checkout,                name='checkout'),
    path('payment/success/',                   views.offer_payment_success,   name='payment_success'),
    path('payment/success/<int:booking_id>/',  views.package_payment_success, name='package_payment_success'),

    # Chat payment requests
    path('chat/payment/<int:pr_id>/accept/',   views.accept_payment_request,  name='accept_payment_request'),
    path('chat/payment/success/',              views.chat_payment_success,    name='chat_payment_success'),
]
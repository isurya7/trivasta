# trivasta/urls.py
from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from trips import views as trip_views

urlpatterns = [
    path('admin/',       admin.site.urls),
    path('',             trip_views.home,    name='home'),
    path('about/',       trip_views.about,   name='about'),
    path('contact/',     trip_views.contact, name='contact'),
    path('privacy/',     trip_views.privacy, name='privacy'),
    path('terms/',       trip_views.terms,   name='terms'),
    path('trips/',       include('trips.urls')),
    path('marketplace/', include('marketplace.urls')),
    path('users/',       include('users.urls')),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
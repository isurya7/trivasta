from django.urls import path
from . import views

urlpatterns = [
    # ── Auth ──────────────────────────────────────────────────────
    path('register/',  views.register_view,  name='register'),
    path('login/',     views.login_view,     name='login'),
    path('logout/',    views.logout_view,    name='logout'),
    path('dashboard/', views.dashboard_view, name='dashboard'),

    # ── Trivasta Admin ────────────────────────────────────────────
    path('admin-dashboard/',                                        views.trivasta_admin,        name='trivasta_admin'),
    path('admin-dashboard/approve/<int:agency_id>/',                views.admin_approve_agency,  name='admin_approve_agency'),
    path('admin-dashboard/reject/<int:agency_id>/',                 views.admin_reject_agency,   name='admin_reject_agency'),
    path('admin-dashboard/reset-warnings/<int:agency_id>/',         views.admin_reset_warnings,  name='admin_reset_warnings'),
    path('admin-dashboard/kyc/<int:agency_id>/verify/',             views.admin_verify_kyc,      name='admin_verify_kyc'),
    path('admin-dashboard/payout/<int:payout_id>/retry/',           views.admin_retry_payout,    name='admin_retry_payout'),

    # ── Support Dashboard ─────────────────────────────────────────
    path('support-dashboard/',                                      views.support_dashboard,     name='support_dashboard'),
    path('support-dashboard/ticket/<int:ticket_id>/',               views.support_ticket_detail, name='support_ticket_detail'),

    # ── Refund Dashboard ──────────────────────────────────────────
    path('refund-dashboard/',                                       views.refund_dashboard,      name='refund_dashboard'),
    path('refund-dashboard/process/<int:refund_id>/',               views.process_refund,        name='process_refund'),
]
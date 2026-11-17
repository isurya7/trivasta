import hashlib
import hmac
from datetime import timedelta
from unittest import mock

from django.contrib.auth.models import User
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from trips.models import Trip

from .models import (
    Agency, AgencyBankDetails, Booking, ChatRoom, Coupon, CouponUsage,
    Message, Offer, Package, PaymentRequest, PayoutRecord,
)
from .payment_confirmation import confirm_booking_payment
from .payment_service import (
    calculate_booking_amounts, transfer_to_agency, verify_payment_signature,
)

SECRET = "test_secret"

PAYMENT_SETTINGS = override_settings(
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    },
    RAZORPAY_KEY_ID="rzp_test_key",
    RAZORPAY_KEY_SECRET=SECRET,
)


def sign(order_id, payment_id):
    body = f"{order_id}|{payment_id}".encode()
    return hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()


class FakeGateway:
    """A stand-in for Razorpay: no network, fully controllable."""

    def __init__(self):
        self.orders = {}
        self.payments = {}
        self.captured = []
        self.orders_created = 0
        self.fail_lookup = False

    def create_order(self, amounts, booking_id, description="Trivasta Booking"):
        self.orders_created += 1
        order_id = f"order_{self.orders_created}"
        self.orders[order_id] = {
            "id": order_id,
            "amount": amounts["total_amount"] * 100,
            "currency": "INR",
            "status": "created",
            "notes": {
                "booking_id": str(booking_id),
                "coupon": amounts.get("coupon_code") or "",
                "discount_amount": str(amounts.get("discount_amount", 0)),
            },
        }
        return self.orders[order_id], None

    def fetch_order(self, order_id):
        if self.fail_lookup:
            raise RuntimeError("gateway down")
        return self.orders[order_id]

    def pay(self, order_id, status="captured", amount=None):
        payment_id = f"pay_{len(self.payments) + 1}"
        self.payments[payment_id] = {
            "id": payment_id,
            "order_id": order_id,
            "status": status,
            "amount": amount if amount is not None else self.orders[order_id]["amount"],
        }
        return payment_id

    def fetch_payment(self, payment_id):
        if self.fail_lookup:
            raise RuntimeError("gateway down")
        return self.payments[payment_id]

    def capture(self, payment_id, amount, *args, **kwargs):
        self.payments[payment_id]["status"] = "captured"
        self.captured.append(payment_id)


@PAYMENT_SETTINGS
class PaymentTestBase(TestCase):
    def setUp(self):
        self.gw = FakeGateway()
        for target, effect in [
            ("marketplace.payment_views.create_razorpay_order", self.gw.create_order),
            ("marketplace.payment_confirmation.fetch_razorpay_order", self.gw.fetch_order),
            ("marketplace.payment_confirmation.fetch_razorpay_payment", self.gw.fetch_payment),
            ("marketplace.payment_confirmation.capture_razorpay_payment", self.gw.capture),
        ]:
            patcher = mock.patch(target, side_effect=effect)
            patcher.start()
            self.addCleanup(patcher.stop)

        self.traveller = User.objects.create_user("trav", "trav@example.com", "pw-123456-x")
        self.agency_user = User.objects.create_user("agent", "agent@example.com", "pw-123456-x")
        self.agency = Agency.objects.create(
            user=self.agency_user, name="Hill Tours", email="hill@example.com",
            phone="+91 90000 00000", status="approved",
        )
        self.package = Package.objects.create(
            agency=self.agency, title="Manali 5D", destination="Manali",
            description="x", duration=5, price=10000, category="adventure",
        )
        self.trip = Trip.objects.create(
            user=self.traveller, destination="Manali", budget=40000, days=5,
            travel_type="couple", num_people=2,
        )
        self.offer = Offer.objects.create(
            agency=self.agency, trip=self.trip, price=30000, message="hi",
        )
        self.client.force_login(self.traveller)

    # ── helpers ───────────────────────────────────────────────────────────
    def callback(self, url, order_id, payment_id, signature=None):
        return self.client.post(url, {
            "razorpay_order_id": order_id,
            "razorpay_payment_id": payment_id,
            "razorpay_signature": signature if signature is not None else sign(order_id, payment_id),
        })

    def make_booking(self, amounts=None, **kwargs):
        amounts = amounts or calculate_booking_amounts(10000)
        fields = dict(
            user=self.traveller, package=self.package, status="pending",
            base_amount=amounts["base_amount"], gst_amount=amounts["gst_amount"],
            commission_amount=amounts["trivasta_commission"],
            agency_payout=amounts["agency_payout"], total_amount=amounts["total_amount"],
        )
        fields.update(kwargs)
        return Booking.objects.create(**fields)

    def make_pr(self, room, amount=10000):
        message = Message.objects.create(
            room=room, sender_type="agency", content="pay", is_payment_request=True,
        )
        return PaymentRequest.objects.create(room=room, message=message, amount=amount)

    def package_room(self):
        return ChatRoom.objects.create(package=self.package, user=self.traveller, agency=self.agency)

    def offer_room(self):
        return ChatRoom.objects.create(offer=self.offer, user=self.traveller, agency=self.agency)

    def start_package_payment(self, package=None):
        package = package or self.package
        self.client.get(reverse("package_book", args=[package.pk]))
        booking = Booking.objects.get(package=package, is_paid=False)
        return booking, booking.razorpay_order_id


class AmountTests(TestCase):
    def test_amounts_use_whole_rupees(self):
        a = calculate_booking_amounts(10000)
        self.assertEqual(a["gst_amount"], 500)
        self.assertEqual(a["total_amount"], 10500)
        self.assertEqual(a["trivasta_commission"], 1000)
        self.assertEqual(a["agency_payout"], 9000)

    def test_gst_and_commission_round_down(self):
        a = calculate_booking_amounts(999)   # 49.95 -> 49, 99.9 -> 99
        self.assertEqual(a["gst_amount"], 49)
        self.assertEqual(a["trivasta_commission"], 99)

    def test_coupon_discount_comes_out_of_agency_share(self):
        coupon = Coupon.objects.create(
            code="SAVE10", discount_value=10,
            valid_from=timezone.now() - timedelta(days=1),
        )
        a = calculate_booking_amounts(10000, coupon)
        self.assertEqual(a["discount_amount"], 1000)
        self.assertEqual(a["base_amount"], 9000)
        self.assertEqual(a["gst_amount"], 450)
        self.assertEqual(a["total_amount"], 9450)
        self.assertEqual(a["trivasta_commission"], 1000)   # on the ORIGINAL price
        self.assertEqual(a["agency_payout"], 8000)

    @PAYMENT_SETTINGS
    def test_signature_check(self):
        self.assertTrue(verify_payment_signature("o1", "p1", sign("o1", "p1")))
        self.assertFalse(verify_payment_signature("o1", "p1", "bad"))
        self.assertFalse(verify_payment_signature("o1", "p1", ""))
        self.assertFalse(verify_payment_signature("o1", "p2", sign("o1", "p1")))


class PackagePaymentTests(PaymentTestBase):
    def test_package_book_reuses_booking_and_order(self):
        url = reverse("package_book", args=[self.package.pk])
        r1 = self.client.get(url)
        r2 = self.client.get(url)
        self.assertEqual(Booking.objects.filter(is_paid=False).count(), 1)
        self.assertEqual(self.gw.orders_created, 1)
        self.assertEqual(r1.context["razorpay_order_id"], r2.context["razorpay_order_id"])
        booking = Booking.objects.get()
        self.assertEqual(booking.total_amount, 10500)
        self.assertEqual(booking.razorpay_order_id, "order_1")

    def test_price_change_creates_fresh_order(self):
        url = reverse("package_book", args=[self.package.pk])
        self.client.get(url)
        self.package.price = 20000
        self.package.save()
        self.client.get(url)
        booking = Booking.objects.get()
        self.assertEqual(booking.total_amount, 21000)
        self.assertEqual(self.gw.orders_created, 2)
        self.assertEqual(booking.razorpay_order_id, "order_2")

    def test_several_unpaid_bookings_do_not_crash_checkout(self):
        for _ in range(2):
            Booking.objects.create(user=self.traveller, package=self.package, status="pending")
        response = self.client.get(reverse("package_book", args=[self.package.pk]))
        self.assertEqual(response.status_code, 200)

    def test_payment_confirms_booking_and_unlocks_contacts(self):
        booking, order_id = self.start_package_payment()
        payment_id = self.gw.pay(order_id)
        url = reverse("package_book_success", args=[self.package.pk])

        response = self.callback(url, order_id, payment_id)
        self.assertRedirects(
            response, reverse("package_booking_confirmation", args=[booking.pk]),
            fetch_redirect_response=False,
        )
        booking.refresh_from_db()
        self.assertTrue(booking.is_paid)
        self.assertEqual(booking.status, "confirmed")
        self.assertEqual(booking.razorpay_payment_id, payment_id)

        payout = PayoutRecord.objects.get(booking=booking)
        self.assertEqual(payout.status, "pending")
        self.assertEqual(payout.total_amount, 10500)
        self.assertEqual(payout.trivasta_commission, 1000)
        self.assertEqual(payout.agency_payout_amount, 9000)

        room = ChatRoom.objects.get(package=self.package, user=self.traveller)
        self.assertTrue(room.messages.filter(sender_type="system",
                                             content__contains=self.agency.phone).exists())
        self.assertEqual(len(mail.outbox), 1)

    def test_replayed_callback_changes_nothing(self):
        booking, order_id = self.start_package_payment()
        payment_id = self.gw.pay(order_id)
        url = reverse("package_book_success", args=[self.package.pk])
        self.callback(url, order_id, payment_id)
        room = ChatRoom.objects.get(package=self.package, user=self.traveller)
        messages_before = room.messages.count()

        response = self.callback(url, order_id, payment_id)
        self.assertRedirects(
            response, reverse("package_booking_confirmation", args=[booking.pk]),
            fetch_redirect_response=False,
        )
        self.assertEqual(room.messages.count(), messages_before)
        self.assertEqual(PayoutRecord.objects.filter(booking=booking).count(), 1)
        self.assertEqual(len(mail.outbox), 1)

    def test_bad_signature_is_rejected(self):
        booking, order_id = self.start_package_payment()
        payment_id = self.gw.pay(order_id)
        url = reverse("package_book_success", args=[self.package.pk])
        response = self.callback(url, order_id, payment_id, signature="bad")
        self.assertRedirects(response, reverse("payment_failed"), fetch_redirect_response=False)
        booking.refresh_from_db()
        self.assertFalse(booking.is_paid)

    def test_payment_cannot_be_used_on_someone_elses_booking(self):
        """The exploit: pay for a cheap booking, replay it on an expensive one."""
        cheap = Package.objects.create(
            agency=self.agency, title="Cheap", destination="Goa",
            description="x", duration=1, price=1000, category="leisure",
        )
        cheap_booking, cheap_order = self.start_package_payment(cheap)
        expensive_booking, _ = self.start_package_payment(self.package)
        payment_id = self.gw.pay(cheap_order)

        response = self.callback(
            reverse("package_payment_success", args=[expensive_booking.pk]),
            cheap_order, payment_id,
        )
        self.assertRedirects(response, reverse("payment_failed"), fetch_redirect_response=False)
        expensive_booking.refresh_from_db()
        self.assertFalse(expensive_booking.is_paid)
        self.assertFalse(PayoutRecord.objects.exists())

    def test_success_url_of_another_package_is_rejected(self):
        other = Package.objects.create(
            agency=self.agency, title="Other", destination="Goa",
            description="x", duration=1, price=50000, category="leisure",
        )
        booking, order_id = self.start_package_payment(self.package)
        payment_id = self.gw.pay(order_id)

        response = self.callback(
            reverse("package_book_success", args=[other.pk]), order_id, payment_id
        )
        self.assertRedirects(response, reverse("payment_failed"), fetch_redirect_response=False)
        booking.refresh_from_db()
        self.assertFalse(booking.is_paid)
        self.assertFalse(Message.objects.filter(room__package=other).exists())

    def test_amount_mismatch_is_rejected(self):
        booking, order_id = self.start_package_payment()
        self.gw.orders[order_id]["amount"] = 100       # tampered / changed order
        payment_id = self.gw.pay(order_id)
        response = self.callback(
            reverse("package_book_success", args=[self.package.pk]), order_id, payment_id
        )
        self.assertRedirects(response, reverse("payment_failed"), fetch_redirect_response=False)
        booking.refresh_from_db()
        self.assertFalse(booking.is_paid)

    def test_payment_for_a_different_order_is_rejected(self):
        booking, order_id = self.start_package_payment()
        payment_id = self.gw.pay(order_id)
        self.gw.payments[payment_id]["order_id"] = "order_other"
        response = self.callback(
            reverse("package_book_success", args=[self.package.pk]), order_id, payment_id
        )
        self.assertRedirects(response, reverse("payment_failed"), fetch_redirect_response=False)
        booking.refresh_from_db()
        self.assertFalse(booking.is_paid)

    def test_failed_payment_is_rejected(self):
        booking, order_id = self.start_package_payment()
        payment_id = self.gw.pay(order_id, status="failed")
        response = self.callback(
            reverse("package_book_success", args=[self.package.pk]), order_id, payment_id
        )
        self.assertRedirects(response, reverse("payment_failed"), fetch_redirect_response=False)
        booking.refresh_from_db()
        self.assertFalse(booking.is_paid)

    def test_authorized_payment_is_captured_then_confirmed(self):
        booking, order_id = self.start_package_payment()
        payment_id = self.gw.pay(order_id, status="authorized")
        self.callback(reverse("package_book_success", args=[self.package.pk]), order_id, payment_id)
        self.assertEqual(self.gw.captured, [payment_id])
        booking.refresh_from_db()
        self.assertTrue(booking.is_paid)

    def test_gateway_outage_is_retryable(self):
        booking, order_id = self.start_package_payment()
        payment_id = self.gw.pay(order_id)
        url = reverse("package_book_success", args=[self.package.pk])

        self.gw.fail_lookup = True
        response = self.callback(url, order_id, payment_id)
        self.assertRedirects(response, reverse("dashboard"), fetch_redirect_response=False)
        booking.refresh_from_db()
        self.assertFalse(booking.is_paid)

        self.gw.fail_lookup = False          # Razorpay is back; the same callback now works
        self.callback(url, order_id, payment_id)
        booking.refresh_from_db()
        self.assertTrue(booking.is_paid)

    def test_order_overwritten_on_booking_still_confirms(self):
        """Booking points at a newer order, but the customer pays the older one."""
        booking, order_id = self.start_package_payment()
        Booking.objects.filter(pk=booking.pk).update(razorpay_order_id="order_999")
        payment_id = self.gw.pay(order_id)
        self.callback(reverse("package_book_success", args=[self.package.pk]), order_id, payment_id)
        booking.refresh_from_db()
        self.assertTrue(booking.is_paid)
        self.assertEqual(booking.razorpay_order_id, order_id)

    def test_order_without_notes_falls_back_to_booking_order_id(self):
        booking, order_id = self.start_package_payment()
        self.gw.orders[order_id]["notes"] = []       # Razorpay sends [] for empty notes
        payment_id = self.gw.pay(order_id)
        self.callback(reverse("package_book_success", args=[self.package.pk]), order_id, payment_id)
        booking.refresh_from_db()
        self.assertTrue(booking.is_paid)


class ConfirmServiceTests(PaymentTestBase):
    def test_second_payment_on_paid_booking_is_flagged(self):
        amounts = calculate_booking_amounts(10000)
        booking = self.make_booking(amounts)
        self.gw.create_order(amounts, booking.pk)
        self.gw.create_order(amounts, booking.pk)
        p1 = self.gw.pay("order_1")
        p2 = self.gw.pay("order_2")

        first = confirm_booking_payment("order_1", p1, sign("order_1", p1))
        second = confirm_booking_payment("order_2", p2, sign("order_2", p2))

        self.assertTrue(first.newly_confirmed)
        self.assertFalse(second.newly_confirmed)
        self.assertTrue(second.duplicate_payment)
        booking.refresh_from_db()
        self.assertEqual(booking.razorpay_payment_id, p1)
        self.assertEqual(PayoutRecord.objects.filter(booking=booking).count(), 1)

    def test_coupon_usage_recorded_from_order_notes(self):
        coupon = Coupon.objects.create(
            code="SAVE10", discount_value=10,
            valid_from=timezone.now() - timedelta(days=1),
        )
        amounts = calculate_booking_amounts(10000, coupon)
        booking = self.make_booking(amounts)
        self.gw.create_order(amounts, booking.pk)
        payment_id = self.gw.pay("order_1")

        for _ in range(2):                    # the second call is a replay
            confirm_booking_payment("order_1", payment_id, sign("order_1", payment_id))

        usage = CouponUsage.objects.get(booking=booking)
        self.assertEqual(usage.discount_applied, 1000)
        coupon.refresh_from_db()
        self.assertEqual(coupon.used_count, 1)
        self.assertEqual(PayoutRecord.objects.get(booking=booking).discount_amount, 1000)


class ChatPaymentTests(PaymentTestBase):
    def start_chat_payment(self, pr):
        response = self.client.get(reverse("accept_payment_request", args=[pr.pk]))
        pr.refresh_from_db()
        return response, pr.razorpay_order_id

    def test_accept_reuses_booking_and_order(self):
        pr = self.make_pr(self.package_room())
        url = reverse("accept_payment_request", args=[pr.pk])
        r1 = self.client.get(url)
        self.client.get(url)
        self.assertEqual(Booking.objects.count(), 1)
        self.assertEqual(self.gw.orders_created, 1)
        pr.refresh_from_db()
        self.assertEqual(pr.razorpay_order_id, "order_1")
        self.assertEqual(r1.context["total"], 10500)
        self.assertContains(r1, "10500 * 100")      # the Pay button's amount renders

    def test_package_chat_payment_creates_its_own_paid_booking(self):
        room = self.package_room()
        pr = self.make_pr(room)
        _, order_id = self.start_chat_payment(pr)
        payment_id = self.gw.pay(order_id)

        response = self.callback(reverse("chat_payment_success"), order_id, payment_id)
        self.assertRedirects(response, reverse("chat_room", args=[room.pk]),
                             fetch_redirect_response=False)

        booking = Booking.objects.get()
        self.assertTrue(booking.is_paid)
        self.assertEqual(booking.package, self.package)
        self.assertIsNone(booking.offer)
        self.assertEqual(booking.total_amount, 10500)
        payout = PayoutRecord.objects.get(booking=booking)
        self.assertEqual(payout.agency_payout_amount, 9000)
        self.assertEqual(payout.trivasta_commission, 1000)

        pr.refresh_from_db()
        self.assertEqual(pr.status, "paid")
        self.assertEqual(pr.razorpay_payment_id, payment_id)
        self.assertTrue(room.messages.filter(sender_type="system",
                                             content__contains=self.agency.email).exists())

    def test_offer_chat_payment_books_the_offer(self):
        room = self.offer_room()
        pr = self.make_pr(room, amount=12000)
        _, order_id = self.start_chat_payment(pr)
        payment_id = self.gw.pay(order_id)
        self.callback(reverse("chat_payment_success"), order_id, payment_id)

        booking = Booking.objects.get()
        self.assertTrue(booking.is_paid)
        self.assertEqual(booking.offer, self.offer)
        self.assertEqual(booking.total_amount, 12600)
        self.assertEqual(booking.base_amount, 12000)     # the NEGOTIATED price, not the offer's

    def test_chat_payment_does_not_touch_an_existing_package_booking(self):
        """The old bug: get_or_create(user, offer=None) grabbed any package booking."""
        old = self.make_booking(
            is_paid=True, status="confirmed",
            razorpay_payment_id="pay_old", razorpay_order_id="order_old",
        )
        pr = self.make_pr(self.package_room())
        _, order_id = self.start_chat_payment(pr)
        payment_id = self.gw.pay(order_id)
        self.callback(reverse("chat_payment_success"), order_id, payment_id)

        self.assertEqual(Booking.objects.count(), 2)
        old.refresh_from_db()
        self.assertEqual(old.razorpay_payment_id, "pay_old")
        new = Booking.objects.exclude(pk=old.pk).get()
        self.assertTrue(new.is_paid)
        self.assertTrue(PayoutRecord.objects.filter(booking=new).exists())

    def test_replayed_chat_callback_changes_nothing(self):
        room = self.package_room()
        pr = self.make_pr(room)
        _, order_id = self.start_chat_payment(pr)
        payment_id = self.gw.pay(order_id)
        url = reverse("chat_payment_success")
        self.callback(url, order_id, payment_id)
        self.callback(url, order_id, payment_id)

        self.assertEqual(Booking.objects.filter(is_paid=True).count(), 1)
        self.assertEqual(PayoutRecord.objects.count(), 1)
        self.assertEqual(room.messages.filter(sender_type="system").count(), 1)

    def test_payment_still_counts_if_request_was_replaced_meanwhile(self):
        room = self.package_room()
        pr = self.make_pr(room)
        _, order_id = self.start_chat_payment(pr)
        PaymentRequest.objects.filter(pk=pr.pk).update(status="rejected")   # agency sent a new one
        payment_id = self.gw.pay(order_id)
        self.callback(reverse("chat_payment_success"), order_id, payment_id)

        pr.refresh_from_db()
        self.assertEqual(pr.status, "paid")
        self.assertTrue(Booking.objects.get().is_paid)

    def test_bad_signature_on_chat_payment(self):
        pr = self.make_pr(self.package_room())
        _, order_id = self.start_chat_payment(pr)
        payment_id = self.gw.pay(order_id)
        response = self.callback(reverse("chat_payment_success"), order_id, payment_id, "bad")
        self.assertRedirects(response, reverse("payment_failed"), fetch_redirect_response=False)
        self.assertFalse(Booking.objects.get().is_paid)

    def test_only_the_room_owner_can_pay_a_request(self):
        pr = self.make_pr(self.package_room())
        stranger = User.objects.create_user("stranger", "s@example.com", "pw-123456-x")
        self.client.force_login(stranger)
        response = self.client.get(reverse("accept_payment_request", args=[pr.pk]))
        self.assertEqual(response.status_code, 404)
        self.assertEqual(Booking.objects.count(), 0)


class OfferCheckoutTests(PaymentTestBase):
    def test_only_the_trip_owner_can_open_checkout(self):
        stranger = User.objects.create_user("stranger", "s@example.com", "pw-123456-x")
        self.client.force_login(stranger)
        response = self.client.get(reverse("checkout", args=[self.offer.pk]))
        self.assertEqual(response.status_code, 404)
        self.assertEqual(Booking.objects.count(), 0)

    def test_already_booked_offer_is_blocked(self):
        Booking.objects.create(user=self.traveller, offer=self.offer, is_paid=True, status="confirmed")
        response = self.client.get(reverse("checkout", args=[self.offer.pk]))
        self.assertRedirects(response, reverse("dashboard"), fetch_redirect_response=False)

    def test_checkout_page_has_the_variables_the_template_needs(self):
        response = self.client.get(reverse("checkout", args=[self.offer.pk]))
        self.assertEqual(response.context["total"], 31500)
        self.assertEqual(response.context["gst_amount"], 1500)
        self.assertContains(response, "31500 * 100")

    def test_offer_payment_confirms_booking(self):
        self.client.get(reverse("checkout", args=[self.offer.pk]))
        booking = Booking.objects.get(offer=self.offer)
        payment_id = self.gw.pay(booking.razorpay_order_id)
        response = self.callback(reverse("payment_success"), booking.razorpay_order_id, payment_id)
        self.assertRedirects(response, reverse("booking_confirmation", args=[booking.pk]),
                             fetch_redirect_response=False)
        booking.refresh_from_db()
        self.assertTrue(booking.is_paid)
        self.assertEqual(PayoutRecord.objects.get(booking=booking).agency_payout_amount, 27000)


class PayoutSafetyTests(PaymentTestBase):
    def amounts_for(self, booking):
        return {
            "total_amount": booking.total_amount, "base_amount": booking.base_amount,
            "gst_amount": booking.gst_amount, "trivasta_commission": booking.commission_amount,
            "agency_payout": booking.agency_payout, "discount_amount": 0,
        }

    def verified_bank(self):
        return AgencyBankDetails.objects.create(
            agency=self.agency, account_holder_name="Hill Tours", account_number="123456789",
            ifsc_code="HDFC0000001", pan_number="ABCDE1234F",
            kyc_status="verified", razorpay_account_id="acc_1",
        )

    def test_nothing_is_sent_while_automatic_payouts_are_off(self):
        booking = self.make_booking(is_paid=True, status="confirmed")
        self.verified_bank()
        with mock.patch("marketplace.payment_service.razorpay_client") as client:
            payout, error = transfer_to_agency(booking, self.amounts_for(booking))
        client.transfer.create.assert_not_called()
        self.assertIn("turned off", error)
        self.assertEqual(payout.status, "pending")

    @override_settings(AUTO_PAYOUTS_ENABLED=True)
    def test_automatic_payout_is_sent_only_once(self):
        booking = self.make_booking(is_paid=True, status="confirmed")
        self.verified_bank()
        amounts = self.amounts_for(booking)
        with mock.patch("marketplace.payment_service.razorpay_client") as client:
            client.transfer.create.return_value = {"id": "trf_1"}
            _, first_error = transfer_to_agency(booking, amounts)
            _, second_error = transfer_to_agency(booking, amounts)
        self.assertEqual(client.transfer.create.call_count, 1)
        self.assertIsNone(first_error)
        self.assertIsNone(second_error)
        payout = PayoutRecord.objects.get(booking=booking)
        self.assertEqual(payout.status, "paid")
        self.assertEqual(payout.razorpay_transfer_id, "trf_1")

    @override_settings(AUTO_PAYOUTS_ENABLED=True)
    def test_payout_already_being_processed_is_not_sent_again(self):
        booking = self.make_booking(is_paid=True, status="confirmed")
        self.verified_bank()
        amounts = self.amounts_for(booking)
        PayoutRecord.objects.create(
            booking=booking, agency=self.agency, total_amount=amounts["total_amount"],
            base_amount=amounts["base_amount"], gst_amount=amounts["gst_amount"],
            trivasta_commission=amounts["trivasta_commission"],
            agency_payout_amount=amounts["agency_payout"], status="processing",
        )
        with mock.patch("marketplace.payment_service.razorpay_client") as client:
            transfer_to_agency(booking, amounts)
        client.transfer.create.assert_not_called()
from unittest import mock

from django.contrib.messages import get_messages
from django.urls import reverse

from .models import Booking, PayoutRecord
from .payment_confirmation import PaymentVerificationError, confirm_verified_payment
from .test_payments import PaymentTestBase


class CaptureRaceTests(PaymentTestBase):
    def start_authorized_payment(self):
        booking, order_id = self.start_package_payment()
        payment_id = self.gw.pay(order_id, status="authorized")
        url = reverse("package_book_success", args=[self.package.pk])
        return booking, order_id, payment_id, url

    def test_capture_error_when_already_captured_still_confirms(self):
        """Razorpay's auto-capture wins the race: our capture call fails, payment is fine."""
        booking, order_id, payment_id, url = self.start_authorized_payment()

        def auto_capture_won(pid, amount, *args, **kwargs):
            self.gw.payments[pid]["status"] = "captured"
            raise RuntimeError("This payment has already been captured")

        with mock.patch("marketplace.payment_confirmation.capture_razorpay_payment",
                        side_effect=auto_capture_won):
            self.callback(url, order_id, payment_id)

        booking.refresh_from_db()
        self.assertTrue(booking.is_paid)

    def test_capture_error_while_still_authorized_is_retryable(self):
        booking, order_id, payment_id, url = self.start_authorized_payment()

        with mock.patch("marketplace.payment_confirmation.capture_razorpay_payment",
                        side_effect=RuntimeError("temporary failure")):
            response = self.callback(url, order_id, payment_id)

        self.assertRedirects(response, reverse("dashboard"), fetch_redirect_response=False)
        booking.refresh_from_db()
        self.assertFalse(booking.is_paid)

        # The same callback works once the capture goes through.
        self.callback(url, order_id, payment_id)
        booking.refresh_from_db()
        self.assertTrue(booking.is_paid)

    def test_retry_message_is_honest(self):
        booking, order_id = self.start_package_payment()
        payment_id = self.gw.pay(order_id)
        self.gw.fail_lookup = True
        response = self.callback(
            reverse("package_book_success", args=[self.package.pk]), order_id, payment_id
        )
        texts = [str(m) for m in get_messages(response.wsgi_request)]
        self.assertEqual(len(texts), 1)
        self.assertIn("don't pay again", texts[0])
        self.assertIn(payment_id, texts[0])
        self.assertIn("support", texts[0])
        self.assertNotIn("shortly", texts[0])


class ManualConfirmationTests(PaymentTestBase):
    def test_confirm_verified_payment_needs_no_signature(self):
        booking, order_id = self.start_package_payment()
        payment_id = self.gw.pay(order_id)

        result = confirm_verified_payment(order_id, payment_id)

        self.assertTrue(result.newly_confirmed)
        booking.refresh_from_db()
        self.assertTrue(booking.is_paid)
        self.assertEqual(PayoutRecord.objects.get(booking=booking).status, "pending")

    def test_confirm_verified_payment_still_checks_the_amount(self):
        booking, order_id = self.start_package_payment()
        self.gw.orders[order_id]["amount"] = 100
        payment_id = self.gw.pay(order_id)

        with self.assertRaises(PaymentVerificationError):
            confirm_verified_payment(order_id, payment_id)
        self.assertFalse(Booking.objects.get(pk=booking.pk).is_paid)

    def test_confirm_verified_payment_twice_is_safe(self):
        booking, order_id = self.start_package_payment()
        payment_id = self.gw.pay(order_id)
        first = confirm_verified_payment(order_id, payment_id)
        second = confirm_verified_payment(order_id, payment_id)
        self.assertTrue(first.newly_confirmed)
        self.assertFalse(second.newly_confirmed)
        self.assertEqual(PayoutRecord.objects.filter(booking=booking).count(), 1)
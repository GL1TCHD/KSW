# -*- coding: utf-8 -*-
"""Backdating and off-list clients (audit 2026-09-28, finding 10).

The phone's clock is corrected by the skew measured on the same call; a
capture recorded in the future, sent too late, or dated into a month already
invoiced/closed is HELD for a dispatcher (never refused: the water is in the
customer's tank). A client outside the driver's list or branch is held too.
"""
from datetime import datetime, timedelta

from freezegun import freeze_time

from odoo import fields
from odoo.tests import tagged

from .test_offline_capture import OfflineCaptureCommon


def _s(dt):
    return fields.Datetime.to_string(dt)


@tagged('post_install', '-at_install')
class TestCaptureTimingAndScope(OfflineCaptureCommon):

    def test_fast_phone_clock_is_corrected(self):
        now = fields.Datetime.now()
        capture = self._receive(
            ref='clk1', sent_at=_s(now + timedelta(hours=2)),
            captured_at=_s(now + timedelta(hours=1)))
        # phone 2 h fast: its "in an hour" was really an hour ago
        self.assertLess(abs((capture.captured_at - (now - timedelta(hours=1)))
                            .total_seconds()), 300)
        self.assertEqual(capture.state, 'issued')

    def test_future_capture_is_held(self):
        now = fields.Datetime.now()
        capture = self._receive(ref='fut1', sent_at=_s(now),
                                captured_at=_s(now + timedelta(hours=3)))
        self.assertEqual(capture.state, 'held')
        self.assertIn('clock', capture.hold_reason)

    def test_capture_sent_too_late_is_held(self):
        now = fields.Datetime.now()
        capture = self._receive(ref='old1', sent_at=_s(now),
                                captured_at=_s(now - timedelta(hours=72)))
        self.assertEqual(capture.state, 'held')
        self.assertIn('hours after the delivery', capture.hold_reason)

    def test_capture_in_an_invoiced_month_is_held(self):
        now = fields.Datetime.now()
        last_month = (now.replace(day=1) - timedelta(days=3))
        Capture = self.Capture
        self.patch(type(Capture), '_falls_in_closed_month', lambda self: True)
        capture = self._receive(ref='cls1', sent_at=_s(now),
                                captured_at=_s(last_month))
        self.assertEqual(capture.state, 'held')
        self.assertIn('already invoiced or closed', capture.hold_reason)

    def test_after_midnight_on_the_first_is_this_month(self):
        """00:30 Riyadh on 1 Feb is 21:30 UTC on 31 Jan. With a posted January
        invoice for the client, reading the month in UTC held the note."""
        user = self.env.user.sudo()
        user.tz = 'Asia/Riyadh'
        captured = datetime(2026, 1, 31, 21, 30)
        # Every month counts as invoiced, so only the month arithmetic decides.
        self.patch(type(self.env['sale.order']), 'search_count', lambda *a, **k: 1)
        with freeze_time(datetime(2026, 1, 31, 21, 35)):
            capture = self.Capture.new({'captured_at': captured, 'partner_id': self.customer.id})
            self.assertFalse(capture._falls_in_closed_month())
            # Same capture an hour EARLIER in local time (23:30 on 31 Jan) is
            # genuinely last month once February has an invoiced order.
            capture.captured_at = datetime(2026, 1, 31, 20, 30)
            with freeze_time(datetime(2026, 2, 1, 6, 0)):
                self.assertTrue(capture._falls_in_closed_month())

    def test_client_off_the_drivers_list_is_held(self):
        self.driver.sudo().x_water_client_ids = [(6, 0, [self.customer.id])]
        other = self.multi_customer if hasattr(self, 'multi_customer') \
            else self.other_branch_customer
        # As the driver: the superuser (and a dispatcher) is exempt.
        capture = self.Capture.with_user(self.driver_user)._receive(
            self._payload(ref='scp1', partner_id=other.id), self.driver)
        self.assertEqual(capture.state, 'held')
        self.assertIn('client list', capture.hold_reason)

    def test_client_on_the_list_issues(self):
        self.driver.sudo().x_water_client_ids = [(6, 0, [self.customer.id])]
        capture = self.Capture.with_user(self.driver_user)._receive(
            self._payload(ref='scp2'), self.driver)
        self.assertEqual(capture.state, 'issued')

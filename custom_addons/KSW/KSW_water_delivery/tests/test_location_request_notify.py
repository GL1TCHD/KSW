"""A client location waiting for approval is announced to the supervisor.

Nothing can be issued for an unlocated client, so a request nobody hears about
blocks the driver at the gate. The guarded properties: the nearest Supervisor
up the driver's reporting line (within the record rule's three levels) gets one
email, a resent pin does not send a second, and a request with nobody to tell
says so on its chatter instead of failing silently.
"""
from odoo.tests import tagged

from .test_water_delivery import HERE, WaterDeliveryCommon


@tagged('post_install', '-at_install')
class TestLocationRequestNotify(WaterDeliveryCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.picking_type.x_location_rule = 'enforce'
        cls.supervisor_user = cls.env['res.users'].create({
            'name': 'Field Supervisor', 'login': 'notify-sup@ksw.test',
            'email': 'notify-sup@ksw.test',
            'group_ids': [(6, 0, [
                cls.env.ref('KSW_water_delivery.group_water_dispatcher').id])],
        })
        cls.supervisor = cls.env['hr.employee'].create({
            'name': 'Field Supervisor', 'user_id': cls.supervisor_user.id,
            'work_email': 'notify-sup@ksw.test'})
        cls.Request = cls.env['ksw.water.location.request'].sudo()
        cls.Mail = cls.env['mail.mail'].sudo()

    def _send(self, ref='n1'):
        return self.env['res.partner'].sudo()._water_receive_pins([{
            'uuid': 'notify-%s' % ref, 'partner_id': self.customer.id,
            'latitude': HERE[0], 'longitude': HERE[1], 'accuracy': 12.0,
            'captured_at': '2026-11-11 08:00:00', 'app_version': '1.0.7',
        }], self.driver)

    def _mails(self):
        return self.Mail.search([('model', '=', 'ksw.water.location.request')])

    def test_the_direct_supervisor_is_emailed(self):
        self.driver.parent_id = self.supervisor
        self._send()
        request = self.Request.search([('uuid', '=', 'notify-n1')])
        mail = self._mails().filtered(lambda m: m.res_id == request.id)
        self.assertEqual(len(mail), 1)
        self.assertEqual(mail.email_to, 'notify-sup@ksw.test')
        self.assertIn(self.customer.display_name, mail.subject)
        action = self.env.ref('KSW_water_delivery.action_water_location_requests')
        self.assertIn('/odoo/action-%d/%d' % (action.id, request.id), mail.body_html)
        self.assertTrue(request.message_ids.filtered(
            lambda m: 'notify-sup@ksw.test' in (m.body or '')))

    def test_a_manager_without_the_role_is_skipped_for_the_next_one(self):
        middle = self.env['hr.employee'].create({
            'name': 'Not A Supervisor', 'work_email': 'middle@ksw.test',
            'parent_id': self.supervisor.id})
        self.driver.parent_id = middle
        self._send()
        self.assertEqual(self._mails().mapped('email_to'), ['notify-sup@ksw.test'])

    def test_a_resent_pin_sends_no_second_email(self):
        self.driver.parent_id = self.supervisor
        self._send()
        self._send()
        self.assertEqual(len(self._mails()), 1)

    def test_nobody_to_tell_is_written_on_the_request(self):
        self.driver.parent_id = False
        self._send()
        self.assertFalse(self._mails())
        request = self.Request.search([('uuid', '=', 'notify-n1')])
        self.assertTrue(request.message_ids.filtered(
            lambda m: 'nobody was notified' in (m.body or '')))

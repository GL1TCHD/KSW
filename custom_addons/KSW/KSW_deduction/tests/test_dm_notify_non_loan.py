# -*- coding: utf-8 -*-
from .common import DeductionCommon


class TestDmNotifyNonLoan(DeductionCommon):
    """A non-loan deduction emails the employee's direct manager when it is
    applied, unless switched off in Settings."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.mgr_user = cls.env['res.users'].create({
            'name': 'KSWDED DM User', 'login': 'kswded_dm_notify',
            'email': 'dm.user@example.com',
        })
        cls.manager_emp.write({
            'user_id': cls.mgr_user.id,
            'work_email': 'dm.work@example.com',
        })

    def _mails(self, ded):
        return self.env['mail.mail'].sudo().search([
            ('model', '=', 'ksw.deduction'), ('res_id', '=', ded.id)])

    def test_default_on_emails_dm(self):
        self.env['ir.config_parameter'].sudo().search([
            ('key', '=', 'KSW_deduction.notify_dm_non_loan')]).unlink()
        ded = self._activate(self._make_deduction())
        mail = self._mails(ded)
        self.assertEqual(len(mail), 1)
        self.assertEqual(mail.email_to, 'dm.work@example.com')
        self.assertIn(self.employee.name, mail.body_html)
        # A plain email, never an inbox notification to the manager.
        self.assertFalse(ded.message_ids.filtered(
            lambda m: self.mgr_user.partner_id in m.partner_ids))

    def test_subject_and_details(self):
        ded = self._make_deduction(
            ded_type=self.type_gov_pen,
            reason='Traffic fine\nRiyadh <ring road>')
        ded.description = 'Paid by company on 1 Nov'
        self._activate(ded)
        mail = self._mails(ded)
        self.assertEqual(mail.subject, '%s - %s - %s' % (
            self.employee.name, self.type_gov_pen.name, ded.x_charge_date))
        self.assertIn('Traffic fine<br>Riyadh &lt;ring road&gt;',
                      mail.body_html.replace('<br/>', '<br>'))
        self.assertIn('Paid by company on 1 Nov', mail.body_html)

    def test_manager_without_user_still_emailed(self):
        boss = self.env['hr.employee'].create({
            'name': 'KSWDED No-User Boss', 'work_email': 'boss@example.com'})
        self.employee_b.parent_id = boss
        ded = self._activate(self._make_deduction(employee=self.employee_b))
        self.assertEqual(self._mails(ded).email_to, 'boss@example.com')

    def test_switched_off_in_settings(self):
        settings = self.env['res.config.settings'].create({})
        self.assertTrue(settings.ksw_notify_dm_non_loan)
        settings.ksw_notify_dm_non_loan = False
        settings.execute()
        self.assertFalse(
            self.env['res.config.settings'].create({}).ksw_notify_dm_non_loan)
        self.assertFalse(self._mails(self._activate(self._make_deduction())))

    def test_loan_not_emailed(self):
        ded = self._activate(self._make_deduction(ded_type=self.type_loan))
        self.assertFalse(self._mails(ded).filtered(
            lambda m: 'Deduction applied' in (m.subject or '')))

    def test_no_manager_no_email(self):
        ded = self._activate(self._make_deduction(employee=self.employee_b))
        self.assertFalse(self._mails(ded))

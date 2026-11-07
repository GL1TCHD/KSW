"""The batch in the ledger, booked as BAS books salaries.

What the parallel run depends on: a month already exported to BAS is never
booked (start date); a batch confirmed one paying account at a time is booked
one confirmation at a time, each payslip once; and a problem with one employee
never holds up the others, nor HR closing the batch.
"""
from datetime import date
from unittest.mock import patch

from odoo.addons.KSW_payroll.tests.test_bas_journal_entry import BasJournalPayrollCommon


class TestPayrollLedger(BasJournalPayrollCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        Account = env['account.account'].sudo()

        def acc(code, kind='expense'):
            return Account.search([('code', '=', code)], limit=1) or Account.create(
                {'code': code, 'name': 'Ledger test %s' % code, 'account_type': kind})
        cls.wage = acc('9919010001')
        cls.saudi_wage = acc('9919010010')
        cls.camp_wage = acc('9919040001')
        cls.housing = acc('9919010002')
        cls.payable = acc('9917010004', 'liability_current')
        params = env['ir.config_parameter'].sudo()
        params.set_param('ksw_payroll_ledger.wage_account', cls.wage.code)
        params.set_param('ksw_payroll_ledger.saudi_wage_account', cls.saudi_wage.code)
        params.set_param('ksw_payroll_ledger.payable_account', cls.payable.code)
        params.set_param('ksw_payroll_ledger.start_date', '2026-03-01')
        cls.rule_hra.sudo().x_ledger_account_id = cls.housing

    def _lines(self, move):
        return {(l.account_id, round(l.balance, 2)) for l in move.line_ids}

    def _confirm(self, slips=None):
        """Confirm as HR does, without recomputing the hand-built lines."""
        def done(recs):
            recs.write({'state': 'done'})
            return True
        with patch.object(type(self.env['hr.payslip']), 'action_payslip_done', done):
            self.batch._ksw_confirm_slips(slips)
        return self.batch.x_ledger_move_ids

    def test_01_earnings_deductions_and_net_as_bas_books_them(self):
        self._payslip([(self.rule_basic, 5000.0), (self.rule_hra, 1250.0),
                       (self.rule_gross, 6250.0), (self.rule_ded, -250.0),
                       (self.rule_net, 6000.0)])
        move = self._confirm()
        self.assertEqual(move.state, 'posted')
        self.assertEqual(move.journal_id.code, 'SAL')
        self.assertEqual(move.date, date(2026, 3, 31))
        # The absence is not a line of its own: the wage is what is paid.
        self.assertEqual(self._lines(move), {
            (self.wage, 4750.0), (self.housing, 1250.0), (self.payable, -6000.0)})

    def test_02_nothing_before_the_start_date(self):
        self.env['ir.config_parameter'].sudo().set_param('ksw_payroll_ledger.start_date', '2026-04-01')
        self._payslip([(self.rule_basic, 5000.0), (self.rule_net, 5000.0)])
        self.assertFalse(self._confirm())
        self.assertFalse(self.batch.x_ledger_error)

    def test_03_no_start_date_posts_nothing(self):
        self.env['ir.config_parameter'].sudo().set_param('ksw_payroll_ledger.start_date', False)
        self._payslip([(self.rule_basic, 5000.0), (self.rule_net, 5000.0)])
        self.assertFalse(self._confirm())

    def test_04_saudi_and_department_wage_accounts(self):
        saudi = self.env['hr.employee'].sudo().create(
            {'name': 'Ledger Saudi', 'country_id': self.env.ref('base.sa').id})
        dept = self.env['hr.department'].sudo().create(
            {'name': 'Ledger Camp', 'x_wage_account_id': self.camp_wage.id})
        camp = self.env['hr.employee'].sudo().create({'name': 'Ledger Camp Man', 'department_id': dept.id})
        self._payslip([(self.rule_basic, 7000.0), (self.rule_net, 7000.0)], employee=saudi)
        self._payslip([(self.rule_basic, 2000.0), (self.rule_net, 2000.0)], employee=camp)
        lines = self._lines(self._confirm())
        self.assertIn((self.saudi_wage, 7000.0), lines)
        self.assertIn((self.camp_wage, 2000.0), lines)

    def test_05_one_employee_never_holds_up_the_others(self):
        self.rule_gosi.sudo().x_bas_posting = 'none'
        other = self.env['hr.employee'].sudo().create({'name': 'Ledger Fine Man'})
        bad = self._payslip([(self.rule_basic, 5000.0), (self.rule_gosi, -500.0),
                             (self.rule_net, 4500.0)])
        good = self._payslip([(self.rule_basic, 3000.0), (self.rule_net, 3000.0)], employee=other)
        move = self._confirm()
        self.assertEqual(good.x_ledger_move_id, move)
        self.assertFalse(bad.x_ledger_move_id)
        self.assertEqual(bad.state, 'done')
        self.assertIn('GOSI', self.batch.x_ledger_error)
        # Fixed, then posted on its own: the good slip is not booked twice.
        self.rule_gosi.sudo().x_bas_posting = 'liability'
        self.batch.action_ksw_ledger_post()
        self.assertTrue(bad.x_ledger_move_id)
        self.assertNotEqual(bad.x_ledger_move_id, move)
        self.assertEqual(good.x_ledger_move_id, move)
        self.assertFalse(self.batch.x_ledger_error)

    def test_06_one_paying_account_at_a_time(self):
        """Mark As Done for some bank accounts: those are booked now, the batch stays draft."""
        other = self.env['hr.employee'].sudo().create({'name': 'Ledger Second Bank'})
        first = self._payslip([(self.rule_basic, 5000.0), (self.rule_net, 5000.0)])
        second = self._payslip([(self.rule_basic, 3000.0), (self.rule_net, 3000.0)], employee=other)
        move = self._confirm(first)
        self.assertEqual(self.batch.state, 'draft')
        self.assertEqual(first.x_ledger_move_id, move)
        self.assertFalse(second.x_ledger_move_id)
        self._confirm(second)
        self.assertEqual(self.batch.state, 'done')
        self.assertTrue(second.x_ledger_move_id)
        self.assertNotEqual(second.x_ledger_move_id, move)
        self.assertEqual(len(self.batch.x_ledger_move_ids), 2)

    def test_07_reopening_the_batch_keeps_the_entries(self):
        """Its slips stay confirmed and paid, so their entries stand."""
        self._payslip([(self.rule_basic, 5000.0), (self.rule_net, 5000.0)])
        move = self._confirm()
        self.batch.write({'state': 'draft'})
        self.assertEqual(move.state, 'posted')
        self.assertEqual(self.batch.x_ledger_move_ids, move)

    def test_08_a_cancelled_payslip_is_reversed_on_its_own(self):
        other = self.env['hr.employee'].sudo().create({'name': 'Ledger Stays'})
        gone = self._payslip([(self.rule_basic, 5000.0), (self.rule_net, 5000.0)])
        stays = self._payslip([(self.rule_basic, 3000.0), (self.rule_net, 3000.0)], employee=other)
        move = self._confirm()
        gone.write({'state': 'cancel'})
        self.assertFalse(gone.x_ledger_move_id)
        self.assertEqual(stays.x_ledger_move_id, move)
        self.assertEqual(move.state, 'posted')
        reversal = self.env['account.move'].search(
            [('journal_id', '=', move.journal_id.id), ('id', '!=', move.id),
             ('ref', 'ilike', gone.name)])
        self.assertEqual(self._lines(reversal), {(self.wage, -5000.0), (self.payable, 5000.0)})

    def test_10_a_payslip_that_nets_to_nothing_is_nothing_to_post(self):
        """An Isuzu driver whose whole basic went to attendance (Oct 2026): BAS books nothing."""
        other = self.env['hr.employee'].sudo().create({'name': 'Ledger Zero Driver'})
        zero = self._payslip([(self.rule_basic, 600.0), (self.rule_gross, 600.0),
                              (self.rule_ded, -600.0), (self.rule_net, 0.0)], employee=other)
        paid = self._payslip([(self.rule_basic, 5000.0), (self.rule_net, 5000.0)])
        move = self._confirm()
        self.assertEqual(paid.x_ledger_move_id, move)
        self.assertFalse(zero.x_ledger_move_id)
        self.assertTrue(zero.x_ledger_nothing)
        self.assertEqual(self.batch.x_ledger_unposted_count, 0)
        self.assertFalse(self.batch.x_ledger_error)

    def test_09_the_bas_export_is_untouched(self):
        """The voucher file still reads the BAS mapping, not the ledger one."""
        self._payslip([(self.rule_basic, 5000.0), (self.rule_hra, 1250.0),
                       (self.rule_gross, 6250.0), (self.rule_net, 6250.0)])
        codes = {r['code'] for r in self.batch._bas_journal_rows()}
        self.assertIn('3101010002', codes)
        self.assertNotIn(self.housing.code, codes)

    def test_11_payroll_user_without_accounting_opens_the_batch(self):
        """a.mahmoud (Nov 2026): Mark As Done for the pending banks, no accounting rights.

        The entries are account.move records; the form must not read them for him."""
        user = self.env['res.users'].sudo().create({
            'name': 'Ledger Payroll Only', 'login': 'ledger_payroll_only',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id,
                                  self.env.ref('om_hr_payroll.group_hr_payroll_manager').id])]})
        self.assertFalse(user.has_group('account.group_account_readonly'))
        self._payslip([(self.rule_basic, 5000.0), (self.rule_net, 5000.0)])
        self.assertTrue(self._confirm())
        Run = self.env['hr.payslip.run'].with_user(user)
        arch = Run.get_views([(False, 'form')])['views']['form']['arch']
        self.assertNotIn('name="x_ledger_move_ids"', arch)
        self.env.invalidate_all()  # the compute must run as him, not come from the cache
        Run.browse(self.batch.id).web_read({
            'x_ledger_move_count': {}, 'x_ledger_unposted_count': {}, 'x_ledger_error': {}})

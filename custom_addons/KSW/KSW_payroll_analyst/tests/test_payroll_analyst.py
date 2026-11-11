# -*- coding: utf-8 -*-
"""Payroll Analyst: every rule fires on its incident and stays quiet
otherwise, the review mark survives an unchanged re-check, and checking a
batch never changes a payroll figure.

Payslips are built by hand (lines written under the system flag) rather
than computed, so each test controls exactly the figures its rule reads.
"""
from calendar import monthrange
from datetime import date
from unittest.mock import patch

from dateutil.relativedelta import relativedelta

from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestPayrollAnalyst(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Payslip = cls.env['hr.payslip'].sudo().with_context(
            ksw_payslip_system=True)
        cls.structure = cls.env.ref('om_hr_payroll.structure_base')
        cls.employee = cls.env['hr.employee'].create({
            'name': 'Analyst Test Employee',
        })
        cls.version = cls.employee.current_version_id
        cls.version.write({
            'date_version': date(2025, 1, 1),
            'contract_date_start': date(2025, 1, 1),
            'wage': 3000.0,
            'struct_id': cls.structure.id,
        })
        cls.d_from = date.today().replace(day=1)
        cls.d_to = cls.d_from.replace(
            day=monthrange(cls.d_from.year, cls.d_from.month)[1])
        cls.prev_from = cls.d_from - relativedelta(months=1)
        cls.prev_to = cls.d_from - relativedelta(days=1)
        cls.batch = cls.env['hr.payslip.run'].sudo().create({
            'name': 'Analyst Test Batch',
            'date_start': cls.d_from,
            'date_end': cls.d_to,
        })
        cls.officer = cls.env['res.users'].create({
            'name': 'Analyst Officer', 'login': 'analyst_officer',
            'group_ids': [(6, 0, [
                cls.env.ref('base.group_user').id,
                cls.env.ref('om_hr_payroll.group_hr_payroll_user').id])],
        })
        cls.plain_user = cls.env['res.users'].create({
            'name': 'Analyst Plain', 'login': 'analyst_plain',
            'group_ids': [(6, 0, [cls.env.ref('base.group_user').id])],
        })

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _rule(self, code):
        rule = self.env['hr.salary.rule'].sudo().search(
            [('code', '=', code)], limit=1)
        self.assertTrue(rule, 'salary rule %s missing' % code)
        return rule

    def _slip(self, lines, run=None, state='draft', d_from=None, d_to=None,
              worked_days=None, inputs=None):
        slip = self.Payslip.create({
            'name': 'Analyst slip',
            'employee_id': self.employee.id,
            'version_id': self.version.id,
            'struct_id': self.structure.id,
            'date_from': d_from or self.d_from,
            'date_to': d_to or self.d_to,
            'payslip_run_id': run.id if run else False,
        })
        Line = self.env['hr.payslip.line'].sudo().with_context(
            ksw_payslip_system=True)
        for seq, (code, amount) in enumerate(lines.items()):
            rule = self._rule(code)
            Line.create({
                'slip_id': slip.id, 'salary_rule_id': rule.id,
                'category_id': rule.category_id.id, 'name': rule.name,
                'code': code, 'sequence': seq, 'employee_id': self.employee.id,
                'version_id': self.version.id,
                'amount': amount, 'quantity': 1.0, 'rate': 100.0,
            })
        for code, (days, amount) in (worked_days or {}).items():
            self.env['hr.payslip.worked_days'].sudo().with_context(
                ksw_payslip_system=True).create({
                    'payslip_id': slip.id, 'name': code, 'code': code,
                    'number_of_days': days, 'amount': amount,
                    'version_id': self.version.id})
        for code, amount in (inputs or {}).items():
            self.env['hr.payslip.input'].sudo().with_context(
                ksw_payslip_system=True).create({
                    'payslip_id': slip.id, 'name': code, 'code': code,
                    'amount': amount, 'version_id': self.version.id})
        if state == 'done':
            slip.write({'state': 'done'})
        return slip

    def _findings(self, batch=None, code=None):
        (batch or self.batch)._ksw_run_analysis()
        found = (batch or self.batch).x_finding_ids
        return found.filtered(lambda f: f.check_code == code) if code else found

    def _installment(self, amount=400.0):
        ded = self.env['ksw.deduction'].create({
            'employee_id': self.employee.id,
            'type_id': self.env.ref('KSW_deduction.type_advance').id,
            'amount': amount, 'installments': 1,
            'start_month': self.d_from, 'reason': 'Analyst test',
        })
        ded.action_submit()
        self.assertEqual(ded.state, 'active')
        return ded.line_ids[:1]

    # ------------------------------------------------------------------
    # NET
    # ------------------------------------------------------------------
    def test_negative_net_is_critical(self):
        self._slip({'BASIC': 1000, 'GROSS': 1000, 'ATTDED': -1500,
                    'NET': -500}, run=self.batch)
        neg = self._findings(code='neg_net')
        self.assertEqual(len(neg), 1)
        self.assertEqual(neg.severity, 'critical')
        self.assertEqual(neg.amount, -500)

    def test_negative_net_on_settlement_is_info(self):
        """A vacation / EOS settlement shows the whole obligation by
        design, so a negative NET there is not an error."""
        slip = self._slip({'BASIC': 1000, 'GROSS': 1000,
                           'KSW_DEDUCTIONS': -3000, 'NET': -2000},
                          run=self.batch)
        HrPayslip = type(self.env['hr.payslip'])
        with patch.object(HrPayslip, '_ksw_shows_full_deductions',
                          lambda self_: self_ == slip):
            neg = self._findings(code='neg_net')
        self.assertEqual(neg.severity, 'info')

    def test_positive_net_has_no_net_finding(self):
        self._slip({'BASIC': 3000, 'GROSS': 3000, 'NET': 3000},
                   run=self.batch)
        found = self._findings()
        self.assertFalse(found.filtered(
            lambda f: f.check_code in ('neg_net', 'zero_net')))

    def test_zero_net_worked_vs_absent(self):
        worked = self._slip({'BASIC': 3000, 'GROSS': 3000, 'ATTDED': -3000,
                             'NET': 0}, run=self.batch,
                            worked_days={'WORK100': (20, 0)})
        zero = self._findings(code='zero_net')
        self.assertEqual(zero.slip_id, worked)
        self.assertEqual(zero.severity, 'warning')
        worked.worked_days_line_ids.filtered(
            lambda w: w.code == 'WORK100').sudo().with_context(
                ksw_payslip_system=True).write({'number_of_days': 0})
        zero = self._findings(code='zero_net')
        self.assertEqual(zero.severity, 'info')

    # ------------------------------------------------------------------
    # Paid twice
    # ------------------------------------------------------------------
    def test_period_already_paid(self):
        self._slip({'BASIC': 3000, 'GROSS': 3000, 'NET': 3000},
                   state='done')
        draft = self._slip({'BASIC': 3000, 'GROSS': 3000, 'NET': 3000},
                           run=self.batch)
        twice = self._findings(code='paid_twice')
        self.assertEqual(twice.slip_id, draft)
        self.assertEqual(twice.severity, 'critical')

    # ------------------------------------------------------------------
    # Deductions vs installments
    # ------------------------------------------------------------------
    def test_settled_installment_missing_from_done_payslip(self):
        """KSWCO SLIP 18441: the installment says this payslip collected
        it, the payslip carries no such deduction."""
        line = self._installment()
        slip = self._slip({'BASIC': 3000, 'GROSS': 3000, 'NET': 3000},
                          run=self.batch, state='done')
        line.sudo().write({'state': 'paid', 'payslip_id': slip.id})
        found = self._findings(code='ded_settled_missing')
        self.assertEqual(found.slip_id, slip)
        self.assertEqual(found.amount, 400)

    def test_deducted_but_installment_pending(self):
        line = self._installment()
        self._slip({'BASIC': 3000, 'GROSS': 3000, 'KSW_DEDUCTIONS': -400,
                    'NET': 2600}, run=self.batch, state='done',
                   inputs={'KSW_DED_%d' % line.id: 400})
        line.sudo().write({'state': 'pending', 'payslip_id': False})
        found = self._findings(code='ded_not_settled')
        self.assertEqual(len(found), 1)
        self.assertEqual(found.amount, 400)

    def test_consistent_settlement_is_quiet(self):
        line = self._installment()
        slip = self._slip({'BASIC': 3000, 'GROSS': 3000,
                           'KSW_DEDUCTIONS': -400, 'NET': 2600},
                          run=self.batch, state='done',
                          inputs={'KSW_DED_%d' % line.id: 400})
        line.sudo().write({'state': 'paid', 'payslip_id': slip.id})
        found = self._findings()
        self.assertFalse(found.filtered(
            lambda f: f.check_code.startswith('ded_')))

    def test_due_installment_missing_from_draft(self):
        line = self._installment(amount=250)
        self._slip({'BASIC': 3000, 'GROSS': 3000, 'NET': 3000},
                   run=self.batch)
        found = self._findings(code='ded_pending_missing')
        self.assertEqual(len(found), 1)
        self.assertEqual(found.amount, 250)
        self.assertIn(line.deduction_id.display_name, found.detail)

    # ------------------------------------------------------------------
    # NET swing, explained by the lines
    # ------------------------------------------------------------------
    def test_net_swing_names_the_line(self):
        self._slip({'BASIC': 3000, 'GROSS': 3000, 'NET': 3000},
                   state='done', d_from=self.prev_from, d_to=self.prev_to)
        self._slip({'BASIC': 3000, 'GROSS': 3000, 'ATTDED': -1500,
                    'NET': 1500}, run=self.batch)
        swing = self._findings(code='net_swing')
        self.assertEqual(len(swing), 1)
        self.assertEqual(swing.amount, -1500)
        self.assertIn(self._rule('ATTDED').name, swing.detail)

    def test_small_change_is_quiet(self):
        self._slip({'BASIC': 3000, 'GROSS': 3000, 'NET': 3000},
                   state='done', d_from=self.prev_from, d_to=self.prev_to)
        self._slip({'BASIC': 3000, 'GROSS': 3000, 'ATTDED': -200,
                    'NET': 2800}, run=self.batch)
        self.assertFalse(self._findings(code='net_swing'))

    # ------------------------------------------------------------------
    # Worked days out of date (and the savepoint that keeps it read-only)
    # ------------------------------------------------------------------
    def test_stale_worked_days_flagged_and_rolled_back(self):
        self._slip({'BASIC': 3000, 'GROSS': 3000, 'NET': 3000},
                   run=self.batch,
                   worked_days={'WORK100': (30, 0), 'ATT_ABS': (0, 0),
                                'ATT_DED': (0, 0)})
        HrPayslip = type(self.env['hr.payslip'])
        env = self.env

        def fake(self_, versions, date_from, date_to):
            # A builder that writes: the analyst must throw it away.
            env['res.partner'].create({'name': 'ANALYST-SIDE-EFFECT'})
            return [
                {'code': 'WORK100', 'name': 'Worked', 'number_of_days': 27},
                {'code': 'ATT_ABS', 'name': 'Absent', 'number_of_days': 3},
                {'code': 'ATT_DED', 'name': 'Deduction', 'amount': 300},
            ]

        with patch.object(HrPayslip, 'get_worked_day_lines', fake):
            stale = self._findings(code='stale_attendance')
        self.assertEqual(len(stale), 1)
        self.assertEqual(stale.amount, -300)
        self.assertFalse(self.env['res.partner'].search(
            [('name', '=', 'ANALYST-SIDE-EFFECT')]))

    def test_check_never_changes_payroll(self):
        line = self._installment()
        slip = self._slip({'BASIC': 3000, 'GROSS': 3000, 'ATTDED': -4000,
                           'NET': -1000}, run=self.batch,
                          worked_days={'WORK100': (30, 0)})
        self.env.flush_all()

        def snapshot():
            self.env.invalidate_all()
            return (
                slip.write_date, slip.state,
                [(l.code, l.amount, l.write_date) for l in slip.line_ids],
                [(w.code, w.number_of_days) for w in
                 slip.worked_days_line_ids],
                [(i.code, i.amount) for i in slip.input_line_ids],
                (line.state, line.payslip_id.id, line.amount),
            )

        before = snapshot()
        self.batch.with_user(self.officer).action_ksw_check_payroll()
        self.env.flush_all()
        self.assertEqual(snapshot(), before)
        self.assertTrue(self.batch.x_finding_ids)

    # ------------------------------------------------------------------
    # Review mark and access
    # ------------------------------------------------------------------
    def test_review_survives_unchanged_recheck_only(self):
        slip = self._slip({'BASIC': 1000, 'GROSS': 1000, 'ATTDED': -1500,
                           'NET': -500}, run=self.batch)
        neg = self._findings(code='neg_net')
        neg.with_user(self.officer).write({'review_note': 'Recovered'})
        neg.with_user(self.officer).action_mark_reviewed()

        again = self._findings(code='neg_net')
        self.assertEqual(again.state, 'reviewed')
        self.assertEqual(again.review_note, 'Recovered')

        slip.line_ids.filtered(lambda l: l.code == 'NET').sudo() \
            .with_context(ksw_payslip_system=True).write({'amount': -700})
        changed = self._findings(code='neg_net')
        self.assertEqual(changed.state, 'open')

    def test_only_officer_checks_or_reviews(self):
        self._slip({'BASIC': 1000, 'GROSS': 1000, 'ATTDED': -1500,
                    'NET': -500}, run=self.batch)
        with self.assertRaises(UserError):
            self.batch.with_user(self.plain_user).action_ksw_check_payroll()
        finding = self._findings(code='neg_net')
        with self.assertRaises(UserError):
            finding.with_user(self.officer).write({'amount': 0})
        with self.assertRaises(UserError):
            finding.with_user(self.plain_user).action_mark_reviewed()

    # ------------------------------------------------------------------
    # Explanation
    # ------------------------------------------------------------------
    def test_explanation_reconciles_and_compares(self):
        self._slip({'BASIC': 3000, 'GROSS': 3000, 'NET': 3000},
                   state='done', d_from=self.prev_from, d_to=self.prev_to)
        slip = self._slip({'BASIC': 3000, 'GROSS': 3000, 'ATTDED': -500,
                           'NET': 2500}, run=self.batch)
        html = str(slip.with_context(lang='en_US').x_explanation_html)
        self.assertIn('Net salary', html)
        self.assertIn('earnings', html)
        self.assertIn(self._rule('ATTDED').name, html)
        self.assertIn('Compared with last month', html)
        self.assertIn('a decrease', html)

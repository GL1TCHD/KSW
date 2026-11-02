"""The month beside its voucher in BAS: by account, then by person."""
from datetime import datetime
from unittest.mock import patch

from odoo.exceptions import UserError

from .test_commission_settlement import SettlementCommon


class TestBasCompare(SettlementCommon):

    def _bas_rows_from(self, rows, number=26008282):
        out = []
        for serial, row in enumerate(rows, 1):
            out.append({
                'SERIAL': serial, 'FDATE': datetime(2026, 8, 31, 9, 5),
                'FCODE': row['code'] if row['debit'] else None,
                'TCODE': row['code'] if row['credit'] else None,
                'AMOUNT': row['debit'] or row['credit'],
                'REF': ' %s' % row['ref'], 'NAME': row['name'],
            })
        return str(number), out

    def _compare(self, run, rows, user=None):
        Wizard = type(self.env['ksw.pay.bas.baseline.wizard'])
        user = user or self.env.ref('base.user_admin')
        with patch.object(Wizard, '_read_bas', lambda w: rows):
            wizard = self.env['ksw.pay.run.bas.compare.wizard'].with_user(
                user).create({'run_id': run.id, 'voucher_number': '26008282'})
            wizard.action_compare()
        # The results are the accountant's page; read them as the system.
        return run.sudo()

    def _month(self, run):
        accounts = run.x_bas_compare_account_ids.filtered(
            lambda a: a.section != 'other')
        return accounts

    def test_01_a_matching_voucher_shows_no_difference(self):
        run = self._approve(self._commission_entry(self.emp_a, 1200.0).batch_id)
        run = self._compare(run, self._bas_rows_from(run._bas_journal_rows()))
        self.assertEqual(run.x_bas_compare_voucher, '26008282')
        accounts = self._month(run)
        self.assertTrue(accounts)
        self.assertFalse(any(accounts.mapped('diff_debit')))
        self.assertFalse(any(accounts.mapped('diff_credit')))
        line = run.x_bas_compare_line_ids.filtered(
            lambda l: l.employee_id == self.emp_a)
        self.assertAlmostEqual(line.odoo_amount, 1200.0)
        self.assertAlmostEqual(line.bas_amount, 1200.0)
        self.assertFalse(line.diff_amount)

    def test_02_bas_only_lines_and_other_accounts_are_shown_apart(self):
        run = self._approve(self._commission_entry(self.emp_a, 1200.0).batch_id)
        rows = run._bas_journal_rows() + [
            # Someone entered in BAS only.
            {'code': '3201010006', 'name': 'x', 'debit': 250.0, 'credit': 0.0,
             'ref': 'بدل عمل اضافي NOBODY KNOWN شهر أغسطس', 'cost_center': ''},
            {'code': '2107010001', 'name': 'y', 'debit': 0.0, 'credit': 250.0,
             'ref': 'عمولات NOBODY KNOWN شهر أغسطس', 'cost_center': ''},
            # The sales commission sharing the voucher.
            {'code': '3203020005', 'name': 'sales', 'debit': 900.0,
             'credit': 0.0, 'ref': 'عمولة مبيعات', 'cost_center': ''},
            {'code': '2107010005', 'name': 'sales due', 'debit': 0.0,
             'credit': 900.0, 'ref': 'عمولة مبيعات', 'cost_center': ''},
        ]
        run = self._compare(run, self._bas_rows_from(rows))
        accounts = self._month(run)
        self.assertAlmostEqual(sum(accounts.mapped('diff_debit')), 250.0)
        self.assertAlmostEqual(sum(accounts.mapped('diff_credit')), 250.0)
        other = run.x_bas_compare_account_ids - accounts
        self.assertEqual(set(other.mapped('code')), {'3203020005', '2107010005'})
        self.assertFalse(any(other.mapped('odoo_debit')))
        stranger = run.x_bas_compare_line_ids.filtered(
            lambda l: not l.employee_id)
        self.assertAlmostEqual(sum(stranger.mapped('diff_amount')), 250.0)
        self.assertIn('NOBODY KNOWN', ' '.join(stranger.mapped('label')))

    def test_03_comparing_again_replaces_the_last_reading(self):
        run = self._approve(self._commission_entry(self.emp_a, 1200.0).batch_id)
        rows = self._bas_rows_from(run._bas_journal_rows())
        run = self._compare(run, rows)
        count = len(run.x_bas_compare_line_ids)
        self._compare(run, rows)
        self.assertEqual(len(run.x_bas_compare_line_ids), count)

    def test_04_only_the_accountant(self):
        run = self._approve(self._commission_entry(self.emp_a, 1200.0).batch_id)
        with self.assertRaises(UserError):
            run.with_user(self.sup_a).action_open_bas_compare_wizard()
        with self.assertRaises(UserError):
            run.with_user(self.sup_a)._bas_compare('26008282')

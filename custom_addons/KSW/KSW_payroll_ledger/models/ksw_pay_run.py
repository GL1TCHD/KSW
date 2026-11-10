"""The paid commission month in Odoo's ledger.

Mark Paid is the commitment (the bank transfer has left), so that is when
the month is booked, in journal COMM, dated the month end like its BAS
voucher. The lines are the voucher's own (``_bas_journal_rows``): the pay
components already name BAS's accounts (3201010004/5/6, 3203020007 ->
accrual 2107010001), so Odoo books exactly what the accountant keys in.
The exported file and its voucher number are left alone.
"""
from markupsafe import Markup

from odoo import _, fields, models
from odoo.exceptions import UserError


class KswPayRun(models.Model):
    _inherit = 'ksw.pay.run'

    x_ledger_move_id = fields.Many2one(
        'account.move', string='Ledger Entry', readonly=True, copy=False,
        help="The month in Odoo's ledger (journal Commissions). Not the BAS export.")
    x_ledger_error = fields.Text(string='Ledger Posting Problem', readonly=True, copy=False)

    def write(self, vals):
        before = {r.id: r.state for r in self} if 'state' in vals else {}
        res = super().write(vals)
        for run in self.filtered(lambda r: before and before[r.id] != 'paid' and r.state == 'paid'):
            run._ksw_ledger_post()
        return res

    def action_ksw_ledger_post(self):
        if not self.env.su and not (self.env.user.has_group('KSW_commissions.group_commission_accountant')
                                    or self.env.user.has_group('account.group_account_manager')):
            raise UserError(_('Only the Commission Accountant may post the month.'))
        for run in self.filtered(lambda r: r.state == 'paid' and not r.x_ledger_move_id):
            run._ksw_ledger_post(raise_errors=True)
        return True

    def _ksw_ledger_post(self, raise_errors=False):
        self.ensure_one()
        Ledger = self.env['ksw.payroll.ledger'].with_context(ksw_ledger_analytic_cache={})
        date = self._bas_journal_date()
        if self.x_ledger_move_id or not Ledger._applies(date):
            return False
        try:
            with self.env.cr.savepoint():
                company = self.env.company
                lines = []
                Line = self.env['account.move.line']
                whose = Line._ksw_employee_by_label(self.line_ids.employee_id)
                for row in self._bas_journal_rows():
                    lines.append((Ledger._account(row['code'], company),
                                  (row['debit'] or 0.0) - (row['credit'] or 0.0),
                                  row['ref'], Ledger._analytic_by_name(row['cost_center']),
                                  Line._ksw_employee_vals(whose(row['ref']))))
                move = Ledger._move(Ledger._journal('COMM', company), date, self.name, lines)
                self.sudo().write({'x_ledger_move_id': move.id or False, 'x_ledger_error': False})
        except UserError as e:
            if raise_errors:
                raise
            self.sudo().write({'x_ledger_error': str(e)})
            Ledger._note(self, Markup('<b>%(t)s</b><br/>%(m)s') % {
                't': _('Not posted to the ledger'), 'm': str(e)})
            return False
        return True

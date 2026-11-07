"""Where payroll and commission figures land in Odoo's ledger, as in BAS.

BAS books a month's salaries as one accrual voucher per paying channel, one
block per employee (September 2026, 26009158 / 26009187):

* his basic wage to the salary-expense account of who he is and where he
  works: Saudis 3201010010, Isuzu drivers 3201010007, Jubail camp 3204010001,
  everyone else 3201010001; a trailer driver carries his trailer as cost
  centre;
* each allowance to its own account: housing 3201010002, transport
  3201010003, mobile 3201010004, other 3201010011;
* deductions are not lines of their own: what he is paid is what is expensed;
* his loan installment credited to his own 1205 account, the net to salaries
  payable 2107010004.

The accounts are configuration — on the salary rule (allowances), on the
department and the employee (the wage), in Settings (the defaults) — so a
department BAS charges somewhere else is one field, not code.
"""
from odoo import _, api, fields, models
from odoo.exceptions import UserError

PARAM_START = 'ksw_payroll_ledger.start_date'
PARAM_WAGE = 'ksw_payroll_ledger.wage_account'
PARAM_SAUDI = 'ksw_payroll_ledger.saudi_wage_account'
PARAM_PAYABLE = 'ksw_payroll_ledger.payable_account'

DEFAULTS = {PARAM_WAGE: '3201010001', PARAM_SAUDI: '3201010010', PARAM_PAYABLE: '2107010004'}


class HrSalaryRule(models.Model):
    _inherit = 'hr.salary.rule'

    x_ledger_account_id = fields.Many2one(
        'account.account', string='Ledger Account', company_dependent=False,
        help='Where this figure lands in Odoo\'s ledger (parallel run). Empty on an '
             'earning: the employee\'s wage account. Separate from the BAS export '
             'mapping.')


class HrDepartment(models.Model):
    _inherit = 'hr.department'

    x_wage_account_id = fields.Many2one(
        'account.account', string='Wage Expense Account',
        help='The salary-expense account BAS charges this department\'s basic '
             'wages to. Empty: the parent department\'s, else the default in Settings.')


class HrEmployee(models.Model):
    _inherit = 'hr.employee'

    x_wage_account_id = fields.Many2one(
        'account.account', string='Wage Expense Account', groups='hr.group_hr_user',
        help='Only for an employee BAS charges somewhere other than his department.')


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    # No config_parameter=: res.config.settings refuses it on a Date field, so
    # the parameter is read and written by hand as 'YYYY-MM-DD'.
    ksw_ledger_start_date = fields.Date(
        string='Post payroll and commissions from',
        help='Batches ending and commission months on or after this date are posted '
             'to the ledger. Earlier ones were exported to BAS and are never posted. '
             'Empty: nothing is posted.')

    @api.model
    def get_values(self):
        res = super().get_values()
        value = self.env['ir.config_parameter'].sudo().get_param(PARAM_START)
        res['ksw_ledger_start_date'] = fields.Date.to_date(value) if value else False
        return res

    def set_values(self):
        super().set_values()
        self.env['ir.config_parameter'].sudo().set_param(
            PARAM_START, fields.Date.to_string(self.ksw_ledger_start_date) or False)


class KswPayrollLedger(models.AbstractModel):
    _name = 'ksw.payroll.ledger'
    _description = 'Payroll and Commissions in the Ledger'

    # ------------------------------------------------------------------ config
    def _param(self, key):
        return self.env['ir.config_parameter'].sudo().get_param(key) or DEFAULTS.get(key)

    def _start_date(self):
        value = self._param(PARAM_START)
        return fields.Date.to_date(value) if value else None

    def _applies(self, date):
        start = self._start_date()
        return bool(start and date and date >= start)

    def _account(self, code, company):
        account = self.env['account.account'].sudo().with_company(company).search(
            [('code', '=', code), ('company_ids', 'in', company.id)], limit=1) if code else None
        if not account:
            raise UserError(_('Account %(code)s does not exist in the chart.', code=code))
        return account

    def _wage_account(self, employee, company):
        """Employee, then his department up the tree, then Saudi / default."""
        employee = employee.sudo()
        if employee.x_wage_account_id:
            return employee.x_wage_account_id
        dept = employee.department_id
        while dept:
            if dept.x_wage_account_id:
                return dept.x_wage_account_id
            dept = dept.parent_id
        saudi = self.env.ref('base.sa', raise_if_not_found=False)
        if saudi and employee.country_id == saudi:
            return self._account(self._param(PARAM_SAUDI), company)
        return self._account(self._param(PARAM_WAGE), company)

    def _vehicle_cost_centre(self, employee):
        """A driver's own vehicle, as BAS charges his wage (T102)."""
        Vehicle = self.env.get('ksw.fleet.vehicle')
        if Vehicle is None or 'driver_id' not in Vehicle._fields:
            return None
        vehicle = Vehicle.sudo().search([('driver_id', '=', employee.id)], limit=1)
        return self._analytic_by_name(vehicle.name) if vehicle else None

    @api.model
    def _analytic_by_name(self, name):
        name = (name or '').strip()
        if not name:
            return None
        cache = self.env.context.get('ksw_ledger_analytic_cache')
        if cache is not None and name in cache:
            return cache[name]
        Analytic = self.env['account.analytic.account'].sudo()
        account = Analytic.search([('name', '=', name)], limit=1) or Analytic.search(
            [('code', '=', name)], limit=1)
        if cache is not None:
            cache[name] = account or None
        return account or None

    # ------------------------------------------------------------------ journals
    @api.model
    def _journal(self, code, company):
        Journal = self.env['account.journal'].sudo()
        journal = Journal.search([('company_id', '=', company.id), ('code', '=', code)], limit=1)
        if journal:
            return journal
        names = {'SAL': ('Payroll', 'الرواتب'), 'COMM': ('Commissions & Allowances', 'العمولات والبدلات')}
        en, ar = names[code]
        journal = Journal.create({'name': en, 'code': code, 'type': 'general', 'company_id': company.id})
        if self.env['res.lang']._lang_get('ar_001'):
            journal.update_field_translations('name', {'ar_001': ar})
        return journal

    # ------------------------------------------------------------------ the entry
    @api.model
    def _move(self, journal, date, ref, lines):
        """``lines``: [(account, amount, label, analytic or None)], +debit / -credit.

        Lines on the same account, label and cost centre are one line. A
        refusal names what is missing rather than balancing on a guess."""
        merged = {}
        for account, amount, label, analytic in lines:
            key = (account.id, label or '', analytic.id if analytic else 0)
            merged[key] = merged.get(key, 0.0) + (amount or 0.0)
        vals = []
        for (account_id, label, analytic_id), amount in merged.items():
            amount = round(amount, 2)
            if not amount:
                continue
            vals.append((0, 0, {
                'account_id': account_id, 'name': label,
                'debit': amount if amount > 0 else 0.0,
                'credit': -amount if amount < 0 else 0.0,
                'analytic_distribution': {str(analytic_id): 100} if analytic_id else False,
            }))
        balance = round(sum(v[2]['debit'] - v[2]['credit'] for v in vals), 2)
        if balance:
            raise UserError(_('The entry does not balance (%(diff).2f).', diff=balance))
        if not vals:
            return self.env['account.move']
        move = self.env['account.move'].sudo().create({
            'move_type': 'entry', 'journal_id': journal.id, 'date': date,
            'ref': ref, 'line_ids': vals,
        })
        move.action_post()
        return move

    @api.model
    def _cancel(self, move):
        move = move.sudo()
        if move.state == 'posted':
            move.button_draft()
        if move.state == 'draft':
            move.button_cancel()

    @api.model
    def _note(self, record, body):
        record.sudo().message_post(body=body, subtype_xmlid='mail.mt_note')

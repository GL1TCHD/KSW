"""Where payroll and commission figures land in Odoo's ledger, as in BAS.

BAS books a month's salaries as one accrual voucher per paying channel, one
block per employee (September 2026, 26009158 / 26009187):

* his basic wage to the salary-expense account of where he works: Isuzu
  drivers 3201010007, Jubail camp 3204010001, everyone else 3201010001; a
  trailer driver carries his trailer as cost centre. BAS keeps some salaries
  apart on 3201010010 («رواتب السعوديين M» — despite the name, not a
  nationality: the IST staff, and anyone else BAS books there for its own
  reasons). Those are the departments that carry it and the employees ticked
  Separate Salary Account;
* each allowance to its own account: housing 3201010002, transport
  3201010003, mobile 3201010004, other 3201010011;
* deductions are not lines of their own: what he is paid is what is expensed;
* his loan installment credited to his own 1205 account, the net to salaries
  payable 2107010004.

Al-Hayat's staff paid from Al-Hayat's own accounts are booked in its H1
accounts, as BAS keys them in its own voucher (26009232): wages 3205010019
(factory; admin and ice workers on their own account), payable 2107010008.
Paid by Al-Kawther they are 3201010010. A Kawther employee paid by Al-Hayat
is expensed on Kawther's side against Al-Hayat's current account 1208010010,
and Al-Hayat carries the payment against Kawther's 2101030003. Whose staff
is the department's (Ledger Entity); whose money is the bank account's.

The accounts are configuration — on the salary rule (allowances), on the
department and the employee (the wage), in Settings (the defaults) — so a
department BAS charges somewhere else is one field, not code.
"""
from odoo import _, api, fields, models
from odoo.exceptions import UserError

PARAM_START = 'ksw_payroll_ledger.start_date'
PARAM_WAGE = 'ksw_payroll_ledger.wage_account'
# Key kept from 19.0.1.0.0, when this was (wrongly) the Saudi-nationality account.
PARAM_SEPARATE = 'ksw_payroll_ledger.saudi_wage_account'
PARAM_PAYABLE = 'ksw_payroll_ledger.payable_account'

PARAM_HAYAT_WAGE = 'ksw_payroll_ledger.hayat_wage_account'
PARAM_HAYAT_PAYABLE = 'ksw_payroll_ledger.hayat_payable_account'
# Each side's current account with the other: in Kawther's books, Al-Hayat
# (1208010010); in Al-Hayat's books, Al-Kawther (2101030003).
PARAM_HAYAT_CURRENT = 'ksw_payroll_ledger.hayat_current_account'
PARAM_KAWTHER_CURRENT = 'ksw_payroll_ledger.kawther_current_account'

DEFAULTS = {
    PARAM_WAGE: '3201010001', PARAM_SEPARATE: '3201010010', PARAM_PAYABLE: '2107010004',
    PARAM_HAYAT_WAGE: '3205010019', PARAM_HAYAT_PAYABLE: '2107010008',
    PARAM_HAYAT_CURRENT: '1208010010', PARAM_KAWTHER_CURRENT: '2101030003',
}

ENTITIES = [('kawther', 'Al-Kawther'), ('hayat', 'Al-Hayat')]


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
    x_ledger_entity = fields.Selection(
        ENTITIES, string='Ledger Entity',
        help='Whose staff this department is, for the books: Al-Hayat\'s are booked in '
             'its H1 accounts when Al-Hayat pays them. Empty: the parent department\'s, '
             'else Al-Kawther.')


class HrEmployee(models.Model):
    _inherit = 'hr.employee'

    x_wage_account_id = fields.Many2one(
        'account.account', string='Wage Expense Account', groups='hr.group_hr_user',
        help='Only for an employee BAS charges somewhere other than his department.')
    x_separate_salary_account = fields.Boolean(
        string='Separate Salary Account', groups='hr.group_hr_user',
        help='His wage is booked to the separate salary account in Settings '
             '(3201010010) instead of his department\'s, whatever the reason BAS '
             'keeps it apart. A Wage Expense Account set on the employee still wins.')


class ResPartnerBank(models.Model):
    _inherit = 'res.partner.bank'

    x_ledger_entity = fields.Selection(
        ENTITIES, string='Ledger Entity',
        help='Whose money a salary paid from this account is: Al-Hayat\'s accounts '
             'are booked with its own payable (2107010008). Empty: Al-Kawther.')


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

    def _entity(self, employee):
        """Whose staff he is: his department's Ledger Entity up the tree."""
        dept = employee.sudo().department_id
        while dept:
            if dept.x_ledger_entity:
                return dept.x_ledger_entity
            dept = dept.parent_id
        return 'kawther'

    def _wage_account(self, employee, company, payer='kawther'):
        """``(account, whole)``: where his wage is booked when ``payer`` pays it.

        ``whole``: his allowances land there too, not on their own accounts, as
        BAS books the separate salary account and Al-Hayat's staff."""
        employee = employee.sudo()
        separate = self._account(self._param(PARAM_SEPARATE), company)
        if self._entity(employee) == 'hayat':
            if payer != 'hayat':
                return separate, True
            account = employee.x_wage_account_id or self._department_wage_account(employee) \
                or self._account(self._param(PARAM_HAYAT_WAGE), company)
            return account, True
        if employee.x_wage_account_id:
            account = employee.x_wage_account_id
        elif employee.x_separate_salary_account:
            account = separate
        else:
            account = self._department_wage_account(employee) \
                or self._account(self._param(PARAM_WAGE), company)
        return account, account == separate

    def _department_wage_account(self, employee):
        dept = employee.department_id
        while dept:
            if dept.x_wage_account_id:
                return dept.x_wage_account_id
            dept = dept.parent_id
        return None

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

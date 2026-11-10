"""Where payroll and commission figures land in Odoo's ledger, as in BAS.

BAS books a month's salaries as one accrual voucher per paying channel, one
block per employee (September 2026, 26009158 / 26009187 / 26009232): his
earnings debited, his deductions netted against them, his loans, other
deductions and GOSI credited to his own account, the net to the paying
entity's salaries payable.

Which account each figure goes to is configuration, in Accounting ▸
Configuration ▸ Payroll Posting (models/posting.py): posting groups,
categories, the posting map, employee exceptions, entities and salaries
paid for another entity. Nothing about a department or an account code is
written here.
"""
from odoo import _, api, fields, models
from odoo.exceptions import UserError

PARAM_START = 'ksw_payroll_ledger.start_date'
PARAM_SUSPENSE = 'ksw_payroll_ledger.suspense_account_id'


class HrSalaryRule(models.Model):
    _inherit = 'hr.salary.rule'

    x_posting_category_id = fields.Many2one(
        'ksw.payroll.posting.category', string='Posting Category',
        help='What this earning is for the books (Housing, Transport…). The posting map '
             "gives the account per posting group. Empty: the employee's wage.")
    x_ledger_account_id = fields.Many2one(
        'account.account', string='Fixed Ledger Account', company_dependent=False,
        help='One account for every employee, whatever his posting group: e.g. '
             'commissions clearing the accrual the commission month booked. Leave empty '
             'for wages and allowances; they follow the posting map.')
    x_ledger_deduction = fields.Selection(
        [('spread', 'Spread over the earnings'),
         ('basic', 'Against the basic wage only'),
         ('own_account', "Employee's own account")],
        string='Deduction in the Ledger', default='spread',
        help="How a deduction is booked. Spread: netted against his wage and allowances "
             "in proportion (six days attended: six days of each). Basic: netted against "
             "the wage line only. Own account: credited to his own account in BAS (Loan "
             "Acc No. in Bas) on a line of its own, e.g. GOSI; without one it is spread.")


class HrDepartment(models.Model):
    _inherit = 'hr.department'

    x_posting_group_id = fields.Many2one(
        'ksw.payroll.posting.group', string='Posting Group',
        help="Which accounts this department's salaries are booked to. Empty: the parent "
             "department's, else the entity's default.")
    x_ledger_entity_id = fields.Many2one(
        'ksw.payroll.entity', string='Ledger Entity',
        help="Whose staff this department is. Empty: the parent department's, else the "
             "default entity.")


class HrEmployee(models.Model):
    _inherit = 'hr.employee'

    x_posting_group_id = fields.Many2one(
        'ksw.payroll.posting.group', string='Posting Group', groups='hr.group_hr_user',
        help="Only for an employee booked apart from his department, whatever the reason "
             "(e.g. Separate Salaries (M)). Empty: his department's.")
    x_posting_exception_ids = fields.One2many(
        'ksw.payroll.employee.posting', 'employee_id', string='Posting Exceptions',
        groups='hr.group_hr_user',
        help='Figures of his that go to another account than his posting group says: '
             'a category → an account; a row without a category moves his whole salary.')


class ResPartnerBank(models.Model):
    _inherit = 'res.partner.bank'

    x_ledger_entity_id = fields.Many2one(
        'ksw.payroll.entity', string='Ledger Entity',
        help='Whose money a salary paid from this account is: its salaries payable. '
             'Empty: the default entity.')


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    # No config_parameter=: res.config.settings refuses it on a Date field, so
    # the parameter is read and written by hand as 'YYYY-MM-DD'.
    ksw_ledger_start_date = fields.Date(
        string='Post payroll and commissions from',
        help='Batches ending and commission months on or after this date are posted '
             'to the ledger. Earlier ones were exported to BAS and are never posted. '
             'Empty: nothing is posted.')
    ksw_ledger_suspense_account_id = fields.Many2one(
        'account.account', string='Payroll Suspense Account',
        help='Where a salary figure goes when no posting group or map row answers, so '
             'the batch still posts and the figure is named on it. Empty: that employee '
             'is held out until the configuration is completed.')

    @api.model
    def get_values(self):
        res = super().get_values()
        value = self.env['ir.config_parameter'].sudo().get_param(PARAM_START)
        res['ksw_ledger_start_date'] = fields.Date.to_date(value) if value else False
        suspense = self.env['ir.config_parameter'].sudo().get_param(PARAM_SUSPENSE)
        res['ksw_ledger_suspense_account_id'] = int(suspense) if suspense and suspense.isdigit() else False
        return res

    def set_values(self):
        super().set_values()
        self.env['ir.config_parameter'].sudo().set_param(
            PARAM_START, fields.Date.to_string(self.ksw_ledger_start_date) or False)
        self.env['ir.config_parameter'].sudo().set_param(
            PARAM_SUSPENSE, str(self.ksw_ledger_suspense_account_id.id or '') or False)


class KswPayrollLedger(models.AbstractModel):
    _name = 'ksw.payroll.ledger'
    _description = 'Payroll and Commissions in the Ledger'

    # ------------------------------------------------------------------ config
    def _param(self, key):
        return self.env['ir.config_parameter'].sudo().get_param(key)

    def _start_date(self):
        value = self._param(PARAM_START)
        return fields.Date.to_date(value) if value else None

    def _applies(self, date):
        start = self._start_date()
        return bool(start and date and date >= start)

    def _suspense_account(self):
        value = self._param(PARAM_SUSPENSE)
        return self.env['account.account'].sudo().browse(int(value)).exists() \
            if value and value.isdigit() else self.env['account.account']

    def _account(self, code, company):
        account = self.env['account.account'].sudo().with_company(company).search(
            [('code', '=', code), ('company_ids', 'in', company.id)], limit=1) if code else None
        if not account:
            raise UserError(_('Account %(code)s does not exist in the chart.', code=code))
        return account

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
        """``lines``: [(account, amount, label, analytic or None[, extra vals])],
        +debit / -credit; ``extra`` is the line's employee and department.

        Lines on the same account, label, cost centre and employee are one
        line. A refusal names what is missing rather than balancing on a guess."""
        merged, extras = {}, {}
        for account, amount, label, analytic, *extra in lines:
            extra = extra[0] if extra else {}
            key = (account.id, label or '', analytic.id if analytic else 0,
                   extra.get('x_employee_id') or 0)
            merged[key] = merged.get(key, 0.0) + (amount or 0.0)
            extras.setdefault(key, extra)
        vals = []
        for key, amount in merged.items():
            account_id, label, analytic_id, _employee = key
            amount = round(amount, 2)
            if not amount:
                continue
            vals.append((0, 0, {
                'account_id': account_id, 'name': label,
                'debit': amount if amount > 0 else 0.0,
                'credit': -amount if amount < 0 else 0.0,
                'analytic_distribution': {str(analytic_id): 100} if analytic_id else False,
                **extras[key],
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

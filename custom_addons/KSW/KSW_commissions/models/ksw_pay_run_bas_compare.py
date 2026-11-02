"""The month next to its voucher in BAS, for the accountant to read.

The month says what it owes; BAS says what was entered for it. Between the
two sit hand edits, vacation payslips and people added in BAS only, and the
accountant used to find them by adding up columns of a pasted voucher
(August 2026: 2,420 more in BAS, which turned out to be two drivers entered
there and not here).

So the month reads its voucher back from BAS (read-only, through
KSW_ext_sync's connector) and lays both side by side: totals by account,
then one line per person. The voucher is matched to people exactly as
"Load BAS Voucher" matches it, so the two never disagree on who a line
belongs to. Lines whose accounts the month does not use (the sales
commission sharing the voucher) are shown apart, not compared.

BAS writes a man's accrual net of what was deducted from him (Dr expense /
Cr advance / Cr accrual for the rest); the Odoo side is laid out the same
way, so the two columns compare account by account.
"""
from collections import defaultdict

from odoo import _, api, fields, models
from odoo.exceptions import UserError

ACCOUNTANT = 'KSW_commissions.group_commission_accountant'

SECTIONS = [
    ('expense', 'Commission Expense'),
    ('accrual', 'Commission Accrual'),
    ('advance', 'Deductions (Advances)'),
    ('other', 'Not Part of the Month'),
]


class KswPayRunBasCompareAccount(models.Model):
    _name = 'ksw.pay.run.bas.compare.account'
    _description = 'Commission Month vs BAS: by Account'
    _order = 'run_id, sequence, code'

    run_id = fields.Many2one(
        'ksw.pay.run', required=True, ondelete='cascade', index=True)
    sequence = fields.Integer()
    section = fields.Selection(SECTIONS, required=True)
    code = fields.Char(string='Account')
    name = fields.Char(string='Account Name')
    currency_id = fields.Many2one(related='run_id.currency_id')
    bas_debit = fields.Monetary(string='BAS Debit')
    bas_credit = fields.Monetary(string='BAS Credit')
    odoo_debit = fields.Monetary(string='Odoo Debit')
    odoo_credit = fields.Monetary(string='Odoo Credit')
    diff_debit = fields.Monetary(
        string='Debit Difference',
        help='BAS minus Odoo. Positive: BAS holds more.')
    diff_credit = fields.Monetary(
        string='Credit Difference',
        help='BAS minus Odoo. Positive: BAS holds more.')


class KswPayRunBasCompareLine(models.Model):
    _name = 'ksw.pay.run.bas.compare.line'
    _description = 'Commission Month vs BAS: by Person'
    _order = 'run_id, sequence, id'

    run_id = fields.Many2one(
        'ksw.pay.run', required=True, ondelete='cascade', index=True)
    sequence = fields.Integer()
    employee_id = fields.Many2one('hr.employee', readonly=True)
    label = fields.Char(
        string='BAS Description',
        help='For a BAS line that names no one the month knows: its own '
             'description.')
    currency_id = fields.Many2one(related='run_id.currency_id')
    odoo_amount = fields.Monetary(
        string='Odoo Commission',
        help='Every approved entry of the month, including the part already '
             'paid on a vacation payslip.')
    odoo_vacation = fields.Monetary(
        string='Of Which on Vacation Payslip')
    odoo_deduction = fields.Monetary(string='Odoo Deductions')
    bas_amount = fields.Monetary(string='BAS Commission')
    bas_deduction = fields.Monetary(string='BAS Deductions')
    diff_amount = fields.Monetary(
        string='Commission Difference', help='BAS minus Odoo.')
    diff_deduction = fields.Monetary(
        string='Deduction Difference', help='BAS minus Odoo.')
    note = fields.Char()


class KswPayRun(models.Model):
    _inherit = 'ksw.pay.run'

    x_bas_compare_voucher = fields.Char(
        string='Compared With BAS Voucher', readonly=True, copy=False)
    x_bas_compare_date = fields.Datetime(
        string='Compared On', readonly=True, copy=False)
    x_bas_compare_by_id = fields.Many2one(
        'res.users', string='Compared By', readonly=True, copy=False)
    x_bas_compare_note = fields.Text(
        string='Comparison Summary', readonly=True, copy=False)
    x_bas_compare_account_ids = fields.One2many(
        'ksw.pay.run.bas.compare.account', 'run_id',
        string='Comparison by Account', readonly=True)
    x_bas_compare_line_ids = fields.One2many(
        'ksw.pay.run.bas.compare.line', 'run_id',
        string='Comparison by Person', readonly=True)

    # ------------------------------------------------------------------
    def _bas_compare_default_voucher(self):
        """The voucher the month was loaded from, else its first export."""
        self.ensure_one()
        if self.x_bas_compare_voucher:
            return self.x_bas_compare_voucher
        vouchers = self.sudo()._bas_vouchers().filtered(
            lambda v: v.state == 'done')
        loaded = vouchers.filtered('bas_voucher_number')[:1]
        if loaded:
            return loaded.bas_voucher_number
        first = vouchers.filtered(
            lambda v: v.origin == 'month' and v.journal_number
        ).sorted('id')[:1]
        return first.journal_number or ''

    def action_open_bas_compare_wizard(self):
        self.ensure_one()
        self.env['ksw.pay.settlement']._check_accountant()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'ksw.pay.run.bas.compare.wizard',
            'view_mode': 'form',
            'target': 'new',
            'name': _('Compare with BAS'),
            'context': {
                'default_run_id': self.id,
                'default_voucher_number': self._bas_compare_default_voucher(),
            },
        }

    # ------------------------------------------------------------------
    def _odoo_compare_figures(self):
        """``(by_person, by_account)`` — the month as BAS should hold it.

        ``by_person``: ``{employee_id: {'amount', 'vacation', 'deduction'}}``
        ``by_account``: ``{(section, code): [name, debit, credit]}``
        """
        self.ensure_one()
        run = self.sudo()
        Settlement = self.env['ksw.pay.settlement'].sudo()
        entries = run._payable_entries(settled_only=True).filtered(
            'employee_id')
        totals = run._rounded_component_totals(entries)
        vacation = run._rounded_component_totals(
            entries.filtered('x_vacation_payslip_id'))
        people = defaultdict(lambda: {'amount': 0.0, 'vacation': 0.0,
                                      'deduction': 0.0})
        accounts = defaultdict(lambda: ['', 0.0, 0.0])
        for employee_id, by_component in totals.items():
            for component, amount in by_component.items():
                people[employee_id]['amount'] += amount
                debit = accounts[('expense', component.x_bas_expense_code or '')]
                debit[0] = debit[0] or component.x_bas_expense_name or ''
                debit[1] += amount
                credit = accounts[('accrual', component.x_bas_accrual_code or '')]
                credit[0] = credit[0] or component.x_bas_accrual_name or ''
                credit[2] += amount
        for employee_id, by_component in vacation.items():
            people[employee_id]['vacation'] += sum(by_component.values())
        # What was taken out of it: credited to his advance, and — as BAS
        # writes it — off his accrual.
        accrual_code, accrual_name = Settlement._month_accrual(run)
        Employee = self.env['hr.employee'].sudo()
        for event in Settlement._desired_events(run).values():
            if event['kind'] != 'deduction':
                continue
            employee_id = event['employee_id']
            people[employee_id]['deduction'] += event['amount']
            try:
                code, name = run._bas_loan_account(Employee.browse(employee_id))
            except UserError:
                # No BAS account on him: still a deduction to compare.
                code, name = '', _('No advance account in BAS')
            advance = accounts[('advance', code or '')]
            advance[0] = advance[0] or name or ''
            advance[2] += event['amount']
            accrual = accounts[('accrual', accrual_code or '')]
            accrual[0] = accrual[0] or accrual_name
            accrual[2] -= event['amount']
        return people, accounts

    def _bas_compare(self, number):
        """Read BAS voucher ``number`` and store the comparison."""
        self.ensure_one()
        self.env['ksw.pay.settlement']._check_accountant()
        run = self.sudo()
        if run.state not in ('approved', 'paid'):
            raise UserError(_('Only an approved or paid month can be compared '
                              'with BAS.'))
        number, account_vals, line_vals, summary = run._bas_compare_values(
            number)
        run.x_bas_compare_account_ids.unlink()
        run.x_bas_compare_line_ids.unlink()
        self.env['ksw.pay.run.bas.compare.account'].sudo().create(account_vals)
        self.env['ksw.pay.run.bas.compare.line'].sudo().create(line_vals)
        # The month's lock is on what the month says, not on a reading of
        # BAS beside it: written as the system, after the accountant check.
        run.write({
            'x_bas_compare_voucher': number,
            'x_bas_compare_date': fields.Datetime.now(),
            'x_bas_compare_by_id': self.env.user.id,
            'x_bas_compare_note': '\n'.join(summary),
        })
        return True

    def _bas_compare_values(self, number):
        """``(number, account rows, person rows, summary lines)`` for BAS
        voucher ``number`` — read only, nothing stored."""
        self.ensure_one()
        run = self.sudo()
        Wizard = self.env['ksw.pay.bas.baseline.wizard'].sudo()
        reader = Wizard.new({'run_id': run.id, 'voucher_number': number})
        number, rows = reader._read_bas()
        # One read: the matcher is handed the same rows.
        matcher = Wizard.with_context(ksw_bas_rows=(number, rows)).new(
            {'run_id': run.id, 'voucher_number': number})
        items, report = matcher._match()

        people, accounts = run._odoo_compare_figures()
        bas_accounts = defaultdict(lambda: ['', 0.0, 0.0])
        other = defaultdict(lambda: ['', 0.0, 0.0])
        skipped = report.get('skipped_serials') or set()
        for row in rows:
            amount = round(row['AMOUNT'] or 0.0, 2)
            code = row['FCODE'] or row['TCODE'] or ''
            debit = bool(row['FCODE'])
            if row['SERIAL'] in skipped:
                target = other[('other', code)]
            else:
                section = ('expense' if debit and not code.startswith(
                    ('2107', '1205')) else 'advance' if code.startswith('1205')
                    else 'accrual')
                target = bas_accounts[(section, code)]
            target[0] = target[0] or ' '.join((row['NAME'] or '').split())
            target[1 if debit else 2] += amount

        order = {key: i for i, (key, _label) in enumerate(SECTIONS)}
        account_vals = []
        for key in sorted(set(accounts) | set(bas_accounts) | set(other),
                          key=lambda k: (order[k[0]], k[1])):
            bas = bas_accounts.get(key) or other.get(key) or ['', 0.0, 0.0]
            odoo = accounts.get(key) or ['', 0.0, 0.0]
            account_vals.append({
                'run_id': run.id, 'sequence': order[key[0]],
                'section': key[0], 'code': key[1],
                'name': bas[0] or odoo[0],
                'bas_debit': bas[1], 'bas_credit': bas[2],
                'odoo_debit': odoo[1], 'odoo_credit': odoo[2],
                'diff_debit': round(bas[1] - odoo[1], 2),
                'diff_credit': round(bas[2] - odoo[2], 2),
            })

        register = set(run.line_ids.employee_id.ids)
        unmatched = [i for i in items if not i.get('employee_id')]
        line_vals = []
        for employee_id in sorted(
                set(people) | set(report['expense']) | set(report['loans']),
                key=lambda e: (self.env['hr.employee'].sudo().browse(e).name
                               or '').strip().casefold()):
            odoo = people.get(employee_id) or {
                'amount': 0.0, 'vacation': 0.0, 'deduction': 0.0}
            bas_amount = round(report['expense'].get(employee_id, 0.0), 2)
            bas_deduction = round(report['loans'].get(employee_id, 0.0), 2)
            diff_amount = round(bas_amount - odoo['amount'], 2)
            diff_deduction = round(bas_deduction - odoo['deduction'], 2)
            notes = []
            if not odoo['amount'] and bas_amount:
                notes.append(_('In BAS only, not in the month'))
            elif odoo['amount'] and not bas_amount:
                notes.append(_('Not in this BAS voucher'))
            if odoo['vacation']:
                notes.append(_('Paid on vacation payslip'))
            if odoo['amount'] and employee_id not in register:
                notes.append(_('Not on the register'))
            if diff_deduction < 0:
                notes.append(_('Deductions posted in another voucher, or not '
                               'yet'))
            line_vals.append({
                'run_id': run.id, 'sequence': 10,
                'employee_id': employee_id,
                'odoo_amount': odoo['amount'],
                'odoo_vacation': odoo['vacation'],
                'odoo_deduction': odoo['deduction'],
                'bas_amount': bas_amount, 'bas_deduction': bas_deduction,
                'diff_amount': diff_amount, 'diff_deduction': diff_deduction,
                'note': ' · '.join(notes),
            })
        for item in unmatched:
            commission = item['kind'] == 'accrual'
            line_vals.append({
                'run_id': run.id, 'sequence': 20,
                'label': item.get('label') or '',
                'bas_amount': item['amount'] if commission else 0.0,
                'bas_deduction': 0.0 if commission else item['amount'],
                'diff_amount': item['amount'] if commission else 0.0,
                'diff_deduction': 0.0 if commission else item['amount'],
                'note': _('Names no one in Odoo'),
            })

        month = [v for v in account_vals if v['section'] != 'other']
        outside = [v for v in account_vals if v['section'] == 'other']
        summary = [
            _('BAS voucher %(number)s', number=number),
            _('This month in BAS: debit %(debit).2f · credit %(credit).2f',
              debit=sum(v['bas_debit'] for v in month),
              credit=sum(v['bas_credit'] for v in month)),
            _('This month in Odoo: debit %(debit).2f · credit %(credit).2f',
              debit=sum(v['odoo_debit'] for v in month),
              credit=sum(v['odoo_credit'] for v in month)),
        ]
        if outside:
            summary.append(_(
                'Also in the voucher, not part of this month (e.g. sales '
                'commission): debit %(debit).2f · credit %(credit).2f',
                debit=sum(v['bas_debit'] for v in outside),
                credit=sum(v['bas_credit'] for v in outside)))
        return number, account_vals, line_vals, summary


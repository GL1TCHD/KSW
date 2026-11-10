"""The payslip batch in Odoo's ledger, booked as BAS books salaries.

Posted as HR confirms it, one paying account at a time (Mark As Done ▸ pick
the bank accounts): each confirmation is one entry per bank account, as BAS
keys one salary voucher per paying channel («رواتب البطاقات», «رواتب
الحسابات الجارية»). Each payslip remembers its entry, so nothing is booked
twice; an employee whose figures cannot be posted (no loan account…) is left
out, named on the batch, and posted later with Post to Ledger. A confirmed
payslip that is cancelled is reversed on its own.

Separate from the BAS voucher export (``_bas_journal_rows``): that file is
what the accountant keys into BAS; this is Odoo's own entry, in journal SAL.
"""
from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.tools import float_round
from odoo.exceptions import UserError


TOTAL_CODES = ('GROSS', 'NET')


class HrPayslip(models.Model):
    _inherit = 'hr.payslip'

    x_ledger_move_id = fields.Many2one(
        'account.move', string='Ledger Entry', readonly=True, copy=False,
        help="The entry this payslip was booked in (journal Payroll). Not the BAS export.")
    x_ledger_nothing = fields.Boolean(
        string='Nothing to Post', readonly=True, copy=False,
        help='Confirmed with every figure netting to zero (e.g. a full attendance '
             'deduction): BAS books nothing for it, and neither does the ledger.')

    def write(self, vals):
        undone = self.filtered(lambda s: s.state == 'done' and s.x_ledger_move_id) \
            if vals.get('state') and vals['state'] != 'done' else self.browse()
        res = super().write(vals)
        for slip in undone.filtered(lambda s: s.state != 'done'):
            slip.payslip_run_id._ksw_ledger_reverse(slip)
        return res


class HrPayslipRun(models.Model):
    _inherit = 'hr.payslip.run'

    # compute_sudo: assigning account.move records to a m2m checks read access on them,
    # and payroll users who confirm the batch have no accounting rights.
    x_ledger_move_ids = fields.Many2many(
        'account.move', string='Ledger Entries', compute='_compute_ledger_moves', compute_sudo=True,
        help="The batch in Odoo's ledger (journal Payroll), one entry per confirmation "
             "and bank account. Not the BAS export.")
    x_ledger_move_count = fields.Integer(compute='_compute_ledger_moves', compute_sudo=True)
    x_ledger_unposted_count = fields.Integer(compute='_compute_ledger_moves', compute_sudo=True)
    x_ledger_error = fields.Text(string='Ledger Posting Problem', readonly=True, copy=False)

    @api.depends('slip_ids.x_ledger_move_id', 'slip_ids.x_ledger_nothing', 'slip_ids.state')
    def _compute_ledger_moves(self):
        for run in self:
            slips = run.slip_ids
            run.x_ledger_move_ids = slips.x_ledger_move_id
            run.x_ledger_move_count = len(slips.x_ledger_move_id)
            run.x_ledger_unposted_count = len(slips.filtered(
                lambda s: s.state == 'done' and not s.x_ledger_move_id and not s.x_ledger_nothing))

    # ------------------------------------------------------------------ hooks
    def _ksw_confirm_slips(self, slips=None):
        res = super()._ksw_confirm_slips(slips)
        for run in self:
            run._ksw_ledger_post()
        return res

    def action_ksw_ledger_post(self):
        """Post every confirmed payslip not booked yet (after fixing what was named)."""
        if not self.env.su and not (self.env.user.has_group('om_hr_payroll.group_hr_payroll_manager')
                                    or self.env.user.has_group('account.group_account_manager')):
            raise UserError(_('Only a Payroll Manager or an accounting administrator may post a batch.'))
        for run in self:
            run._ksw_ledger_post()
        return True

    # ------------------------------------------------------------------ posting
    def _ksw_ledger_pending(self):
        self.ensure_one()
        return self.slip_ids.filtered(
            lambda s: s.state == 'done' and not s.x_ledger_move_id and not s.x_ledger_nothing)

    def _ksw_ledger_post(self):
        """Book the confirmed, unbooked payslips: one entry per bank account."""
        self.ensure_one()
        Ledger = self.env['ksw.payroll.ledger']
        pending = self._ksw_ledger_pending()
        if not pending or not Ledger._applies(self.date_end):
            return False
        groups = {}
        for slip in pending:
            bank = self._ksw_ledger_bank(slip)
            groups.setdefault(bank, self.env['hr.payslip'])
            groups[bank] |= slip
        problems = []
        journal = Ledger._journal('SAL', self.env.company)
        for bank, slips in groups.items():
            lines, posted_slips, bad, zero = self._ksw_ledger_lines(slips)
            problems += bad
            if zero:
                zero.sudo().write({'x_ledger_nothing': True})
                posted_slips -= zero
            if not posted_slips:
                continue
            ref = ' · '.join(filter(None, [self.name, bank]))
            try:
                with self.env.cr.savepoint():
                    move = Ledger._move(journal, self.date_end, ref, lines)
                    posted_slips.sudo().write({'x_ledger_move_id': move.id})
            except UserError as e:
                problems.append(str(e))
        error = '\n'.join(problems) if problems else False
        if error != (self.x_ledger_error or False):
            self.sudo().write({'x_ledger_error': error})
            if error:
                Ledger._note(self, Markup('<b>%(t)s</b><br/>%(m)s') % {
                    't': _('Some payslips were not posted to the ledger'),
                    'm': Markup('<br/>').join(problems[:30])})
        return True

    def _ksw_ledger_bank(self, slip):
        """The paying account's label, as the Mark As Done wizard groups them."""
        bank = self._resolve_slip_bank_account(slip)
        return bank.sudo().display_name if bank else ''

    def _ksw_ledger_payer(self, slip):
        """Whose money paid him: the paying account's Ledger Entity."""
        return self.env['ksw.payroll.posting']._payer_entity(
            self._resolve_slip_bank_account(slip), self.env.company)

    def _ksw_ledger_reverse(self, slip):
        """A booked payslip taken back: its own lines, the other way round."""
        Ledger = self.env['ksw.payroll.ledger']
        # sudo: the payroll user cancelling a payslip need not read journal entries.
        move = slip.x_ledger_move_id.sudo()
        if not self:
            slip.sudo().write({'x_ledger_move_id': False})
            return
        lines, posted, _bad, _zero = self._ksw_ledger_lines(slip)
        if posted:
            Ledger._move(move.journal_id, fields.Date.context_today(self),
                         _('Reversal of %(slip)s (%(move)s)', slip=slip.name, move=move.name),
                         [(a, -amt, label, an, *extra) for a, amt, label, an, *extra in lines])
        slip.sudo().write({'x_ledger_move_id': False})

    # ------------------------------------------------------------------ lines
    def _ksw_ledger_lines(self, slips=None):
        """(lines, slips handled, problems, slips that net to nothing), one block per employee.

        Each figure's account comes from the payroll posting configuration
        (his posting group's map, his exceptions); the net goes to the paying
        entity's salaries payable, or through the current accounts of Paid for
        Another Entity. Each block is checked against its NET."""
        self.ensure_one()
        Journal = self.env['ksw.bas.journal']
        lang = Journal.voucher_lang()
        if lang and self.env.context.get('lang') != lang:
            return self.with_context(lang=lang)._ksw_ledger_lines(slips)
        Posting = self.env['ksw.payroll.posting']
        company = self.env.company
        month = Journal.month_label(self.date_end)
        slips = self.slip_ids if slips is None else slips
        lines, done, problems, zero = [], self.env['hr.payslip'], [], self.env['hr.payslip']
        if not Posting._default_entity(company):
            return lines, done, [_('No default payroll entity in Accounting ▸ Configuration ▸ '
                                   'Payroll Posting ▸ Entities.')], zero
        for slip in slips.filtered(lambda s: s.state != 'cancel' or s.x_ledger_move_id).sorted(
                lambda s: (s.employee_id.sudo().name or '', s.id)):
            if not slip.line_ids:
                continue
            employee = slip.employee_id.sudo()
            payer = self._ksw_ledger_payer(slip)
            staff = Posting._staff_entity(employee, company)
            group = Posting._posting_group(employee, staff, payer)
            intercompany = Posting._intercompany(payer, staff)
            label = Journal.ref(_('Salary'), Journal.employee_label(employee), month)
            try:
                if not group:
                    raise UserError(_('no posting group'))
                block, net = self._ksw_ledger_block(slip, employee, company, month, group)
                if intercompany.payer_account_id and intercompany.staff_account_id:
                    settle = [
                        (intercompany.staff_account_id, -net, label, None),
                        (intercompany.payer_account_id, net, label, None),
                        (payer.payable_account_id, -net, label, None),
                    ]
                else:
                    settle = [(payer.payable_account_id, -net, label, None)]
            except UserError as e:
                problems.append('%s: %s' % (employee.name, e))
                continue
            per_account = {}
            for account, amount, _label, _an in block + settle:
                per_account[account.id] = per_account.get(account.id, 0.0) + amount
            done |= slip
            if not any(round(v, 2) for v in per_account.values()):
                zero |= slip
                continue
            suspense = self.env['ksw.payroll.ledger']._suspense_account()
            if suspense and suspense.id in per_account:
                problems.append(_('%(name)s: posted to the suspense account %(acc)s, no posting '
                                  'map row for some of his figures', name=employee.name,
                                  acc=suspense.code))
            who = self.env['account.move.line']._ksw_employee_vals(employee)
            lines += [line + (who,) for line in block + settle]
        return lines, done, problems, zero

    def _ksw_ledger_account(self, employee, group, category):
        """The map's answer, else the suspense account, else a refusal."""
        account = self.env['ksw.payroll.posting']._account(employee, group, category)
        if account:
            return account
        suspense = self.env['ksw.payroll.ledger']._suspense_account()
        if suspense:
            return suspense
        raise UserError(_('posting group %(group)s has no account for %(what)s',
                          group=group.name, what=category.name if category else _('the wage')))

    def _ksw_ledger_block(self, slip, employee, company, month, group):
        """His lines, and the net they must come to.

        Earnings debited to the accounts the posting configuration gives;
        deductions netted against them (in proportion, or against the wage
        only) or credited to his own account; his loans and other deductions
        credited to his own account, a line each."""
        Journal = self.env['ksw.bas.journal']
        Ledger = self.env['ksw.payroll.ledger'].with_context(
            ksw_ledger_analytic_cache=self.env.context.get('ksw_ledger_analytic_cache', {}))
        who = Journal.employee_label(employee)
        wage = self._ksw_ledger_account(employee, group, False)
        cost_centre = Ledger._vehicle_cost_centre(employee)
        salary_label = Journal.ref(_('Salary'), who, month)
        earnings, credits, spread, against_wage, net, unmapped = [], [], 0.0, 0.0, 0.0, []
        for line in slip.line_ids:
            amount = line.total or 0.0
            rule = line.salary_rule_id
            posting = rule.x_bas_posting
            treatment = rule.x_ledger_deduction or 'spread'
            if posting == 'payable':
                net = amount
                continue
            if not amount or line.code in TOTAL_CODES:
                continue
            if posting == 'loan' or (posting == 'liability' and treatment == 'own_account'
                                     and (employee.x_loan_acc_no or '').strip()):
                try:
                    code, _name = Journal.loan_account(employee)
                    own = Ledger._account(code, company)
                except UserError:
                    unmapped.append(_('no loan account'))
                    continue
                for label, part in self._ksw_ledger_credit_parts(slip, line, rule, who, month):
                    credits.append((own, part, label, None))
            elif rule.x_ledger_account_id:
                earnings.append([rule.x_ledger_account_id, amount, salary_label, None])
            elif posting == 'expense':
                account = self._ksw_ledger_account(employee, group, rule.x_posting_category_id)
                earnings.append([account, amount, salary_label, cost_centre if account == wage else None])
            elif posting == 'liability':
                if treatment == 'basic':
                    against_wage += amount
                else:
                    spread += amount
            else:
                unmapped.append(rule.code or rule.name)
        self._ksw_ledger_spread(earnings, spread, wage, salary_label, cost_centre)
        if round(against_wage, 2):
            line = next((e for e in earnings if e[0] == wage), None)
            if line:
                line[1] += against_wage
            else:
                earnings.append([wage, against_wage, salary_label, cost_centre])
        block = [tuple(e) for e in earnings] + credits
        posted = round(sum(b[1] for b in block), 2)
        if unmapped or abs(posted - round(net, 2)) >= 0.005:
            raise UserError(_('payslip net %(net).2f, entry %(posted).2f%(rules)s',
                              net=net, posted=posted,
                              rules=(' (' + ', '.join(unmapped) + ')') if unmapped else ''))
        return block, net

    @api.model
    def _ksw_ledger_spread(self, earnings, netted, wage, label, cost_centre):
        """Deductions netted against the earnings in proportion to them.

        Six days attended of thirty-one: six thirty-firsts of the wage and of
        each allowance, not the whole allowance and what is left of the wage.
        Only expense lines share it (a commission clearing its accrual does
        not); the wage line takes the rounding."""
        if not round(netted, 2):
            return
        shared = [e for e in earnings if (e[0].account_type or '').startswith('expense')]
        base = sum(e[1] for e in shared)
        if not shared or not base:
            earnings.append([wage, netted, label, cost_centre])
            return
        digits = 0 if all(float(e[1]).is_integer() for e in shared) and float(netted).is_integer() else 2
        anchor = next((e for e in shared if e[0] == wage), max(shared, key=lambda e: e[1]))
        given = 0.0
        for e in shared:
            if e is anchor:
                continue
            part = float_round(netted * e[1] / base, precision_digits=digits)
            e[1] += part
            given += part
        anchor[1] += netted - given

    def _ksw_ledger_credit_parts(self, slip, line, rule, who, month):
        """[(label, amount)] for one credit to his own account.

        KSW_DEDUCTIONS is the sum of one input per installment: split by what
        each one is (loan installment, salary advance, penalty…), so the
        account shows them apart. Any other rule is one line under its name."""
        Journal = self.env['ksw.bas.journal']
        amount = line.total or 0.0
        if line.code != 'KSW_DEDUCTIONS':
            return [(Journal.ref(rule.name or rule.code, who, month), amount)]
        Line = self.env['ksw.deduction.line'].sudo()
        parts = {}
        for inp in slip.input_line_ids.filtered(lambda i: (i.code or '').startswith('KSW_DED')):
            ident = (inp.code or '').rsplit('_', 1)[-1]
            ded = Line.browse(int(ident)).exists().deduction_id if ident.isdigit() else Line.deduction_id
            if ded.is_loan:
                kind = _('Loan installment')
            else:
                kind = ded.type_id.name or _('Deducted from salary')
            parts[kind] = parts.get(kind, 0.0) - (inp.amount or 0.0)
        if not parts or abs(sum(parts.values()) - amount) >= 0.005:
            return [(Journal.ref(_('Deducted from salary'), who, month), amount)]
        return [(Journal.ref(kind, who, month), part) for kind, part in parts.items() if round(part, 2)]

"""Deterministic payroll checks.

Every check here reads; none of them computes, confirms, cancels or writes
a payslip. Each rule is one past KSW payroll incident turned into a query,
so a finding always names the records and the figures behind it — there is
no model guessing anything.

The one check that has to call payroll code (``stale_attendance`` calls
``get_worked_day_lines``, which is read-only today) runs inside a savepoint
that is always rolled back, so it stays read-only even if that code starts
writing one day.
"""
import logging

from dateutil.relativedelta import relativedelta
from markupsafe import Markup

from odoo import api, models, _
from odoo.tools import float_is_zero
from odoo.tools.misc import formatLang

from .payroll_finding import SEVERITY_RANK

_logger = logging.getLogger(__name__)

# A NET change worth a look: at least this share of last month's NET AND
# at least this many riyals. Both, so a 40 SAR change on a 100 SAR slip and
# a 600 SAR change on a 20,000 SAR slip are left alone.
SWING_RATIO = 0.25
SWING_MIN_AMOUNT = 500.0

# Worked-day figures compared by the stale-attendance check:
# code -> (field, tolerance)
# WORK100 is left out on purpose: its day count does not reach the money
# (BASIC is the full wage, ATTDED takes the absences off), and comparing it
# flagged 300 of 403 slips on a counting change alone.
_WD_COMPARED = {
    'ATT_ABS': ('number_of_days', 0.5),
    'ATT_DED': ('amount', 1.0),
}

_TOTAL_CODES = ('GROSS', 'NET')


class _Rollback(Exception):
    """Raised inside a savepoint to throw everything in it away."""


class KswPayrollAnalyst(models.AbstractModel):
    _name = 'ksw.payroll.analyst'
    _description = 'Payroll Analyst'

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------
    @api.model
    def _analyze(self, run):
        """Return the finding values for a payslip batch."""
        run.ensure_one()
        return self._analyze_slips(run.sudo().slip_ids, run=run)

    @api.model
    def _analyze_slips(self, slips, run=None):
        """Return the finding values for ``slips`` (a list of dicts).

        With ``run``, the batch-level checks run too (missing employees).
        Without, the slips are checked on their own: the vacation / EOS
        settlements that live outside any batch.
        """
        ctx = self._build_context(slips, run)
        findings = []
        for check in (
            self._check_net,
            self._check_duplicate_in_batch,
            self._check_superseded,
            self._check_done_deductions,
            self._check_pending_deductions,
            self._check_stale_attendance,
            self._check_sheet_unconfirmed,
            self._check_net_swing,
            self._check_no_bank,
            self._check_missing_employee,
        ):
            findings += check(ctx)
        for vals in findings:
            vals['run_id'] = run.id if run else False
            vals['severity_rank'] = SEVERITY_RANK[vals['severity']]
        return findings

    # ------------------------------------------------------------------
    # Shared data
    # ------------------------------------------------------------------
    def _build_context(self, slips, run=None):
        slips = slips.sudo().filtered(lambda s: s.state != 'cancel')
        ctx = {
            'run': run.sudo() if run else None,
            'slips': slips,
            'draft': slips.filtered(lambda s: s.state == 'draft'),
            'done': slips.filtered(lambda s: s.state == 'done'),
            'currency': self.env.company.currency_id,
            'previous_pool': {},
            'sheet_state': {},
        }
        if not slips:
            return ctx
        # Confirmed monthly payslips of the month before, in one search.
        # A batch also needs everybody's (missing-employee check).
        first = min(slips.mapped('date_from'))
        if run:
            first = min(first, run.date_start)
        domain = [
            ('state', '=', 'done'),
            ('x_is_revision', '=', False),
            ('x_leave_id', '=', False),
            ('credit_note', '=', False),
            ('date_from', '>=', first - relativedelta(months=1)),
            ('date_to', '<', max(slips.mapped('date_from'))),
        ]
        if not run:
            domain.append(('employee_id', 'in', slips.employee_id.ids))
        pool = ctx['previous_pool']
        for prev in self.env['hr.payslip'].sudo().search(
                domain, order='date_to desc, id desc'):
            pool.setdefault(prev.employee_id.id, []).append(prev)
        # Attendance-sheet state per draft monthly payslip.
        Sheet = self.env['ksw.attendance.sheet'].sudo()
        for slip in ctx['draft'].filtered(
                lambda s: self._is_monthly(s)
                and s.employee_id.x_is_attendance_sheet):
            sheet = Sheet.search([
                ('employee_id', '=', slip.employee_id.id),
                ('year', '=', slip.date_from.year),
                ('month', '=', str(slip.date_from.month)),
            ], limit=1)
            ctx['sheet_state'][slip.id] = sheet
        return ctx

    @api.model
    def _previous_for(self, ctx, slip):
        """The employee's confirmed monthly payslip of the month before
        ``slip``, or an empty recordset."""
        since = slip.date_from - relativedelta(months=1)
        for prev in ctx['previous_pool'].get(slip.employee_id.id, []):
            if prev != slip and prev.date_to < slip.date_from \
                    and prev.date_from >= since:
                return prev
        return self.env['hr.payslip']

    @api.model
    def _is_monthly(self, slip):
        """An ordinary monthly payslip: not a revision, not a vacation /
        EOS settlement, not a refund."""
        return not (slip.x_is_revision or slip.x_leave_id or slip.credit_note)

    @api.model
    def _previous_slips(self, employees, before, since=None):
        """``{employee_id: payslip}`` — each employee's latest confirmed
        monthly payslip ending before ``before`` (and, if given, starting on
        or after ``since``)."""
        domain = [
            ('state', '=', 'done'),
            ('date_to', '<', before),
            ('x_is_revision', '=', False),
            ('x_leave_id', '=', False),
            ('credit_note', '=', False),
        ]
        if since:
            domain.append(('date_from', '>=', since))
        if employees is not None:
            domain.append(('employee_id', 'in', employees.ids))
        result = {}
        for slip in self.env['hr.payslip'].sudo().search(
                domain, order='date_to desc, id desc'):
            result.setdefault(slip.employee_id.id, slip)
        return result

    @api.model
    def _totals(self, slip):
        """``{code: (name, total)}`` for the payslip's computed lines."""
        out = {}
        for line in slip.line_ids:
            name, total = out.get(line.code, (line.name, 0.0))
            out[line.code] = (name, total + line.total)
        return out

    @api.model
    def _net(self, slip):
        return self._totals(slip).get('NET', ('', 0.0))[1]

    @api.model
    def _worked_days(self, slip):
        out = {}
        for wd in slip.worked_days_line_ids:
            entry = out.setdefault(wd.code, {
                'name': wd.name, 'number_of_days': 0.0,
                'number_of_hours': 0.0, 'amount': 0.0})
            entry['number_of_days'] += wd.number_of_days or 0.0
            entry['number_of_hours'] += wd.number_of_hours or 0.0
            entry['amount'] += wd.amount or 0.0
        return out

    # ------------------------------------------------------------------
    # Formatting
    # ------------------------------------------------------------------
    def _money(self, ctx, value):
        return formatLang(self.env, value, currency_obj=ctx['currency'])

    @api.model
    def _slip_ref(self, slip):
        return slip.number or slip.name or str(slip.id)

    @api.model
    def _html_table(self, head, rows):
        """An HTML table; every cell is escaped unless already Markup."""
        th = Markup('').join(Markup('<th>%s</th>') % h for h in head)
        body = Markup('').join(
            Markup('<tr>%s</tr>') % Markup('').join(
                Markup('<td>%s</td>') % c for c in row)
            for row in rows)
        return Markup(
            '<table class="table table-sm table-bordered mb-1">'
            '<thead><tr>%s</tr></thead><tbody>%s</tbody></table>'
        ) % (th, body)

    @api.model
    def _para(self, text):
        return Markup('<p class="mb-1">%s</p>') % text

    def _finding(self, code, severity, slip, title, detail, amount=0.0,
                 key_suffix=''):
        return {
            'check_code': code,
            'severity': severity,
            'slip_id': slip.id if slip else False,
            'employee_id': slip.employee_id.id if slip else False,
            'title': title,
            'detail': detail,
            'amount': amount,
            'key': '%s:%s%s' % (code, slip.id if slip else '',
                                (':%s' % key_suffix) if key_suffix else ''),
        }

    def _frozen_note(self, slip):
        if slip.x_bank_exported_date:
            return self._para(Markup('<strong>%s</strong>') % _(
                'This payslip was already sent to the bank on %(date)s.',
                date=slip.x_bank_exported_date.date()))
        return Markup('')

    # ------------------------------------------------------------------
    # 1. NET below or at zero
    # ------------------------------------------------------------------
    def _check_net(self, ctx):
        out = []
        for slip in ctx['slips']:
            totals = self._totals(slip)
            if 'NET' not in totals:
                if slip.state == 'draft':
                    out.append(self._finding(
                        'zero_net', 'warning', slip,
                        _('Payslip not computed'),
                        self._para(_('This payslip has no salary lines, so '
                                     'nothing will be paid. Press Compute '
                                     'Sheet or remove it from the batch.'))))
                continue
            net = totals['NET'][1]
            gross = totals.get('GROSS', ('', 0.0))[1]
            if net > 0.005:
                continue
            deductions = sorted(
                ((name, total) for code, (name, total) in totals.items()
                 if total < 0 and code not in _TOTAL_CODES),
                key=lambda r: r[1])
            rows = [(name, self._money(ctx, total))
                    for name, total in deductions]
            wd = self._worked_days(slip)
            absent = wd.get('ATT_ABS', {}).get('number_of_days', 0.0)
            detail = self._para(_(
                'Gross %(gross)s, NET %(net)s.',
                gross=self._money(ctx, gross), net=self._money(ctx, net)))
            if rows:
                detail += self._html_table([_('Deduction'), _('Amount')], rows)
            if absent:
                detail += self._para(_(
                    '%(days)s absent days on the worked days.',
                    days='%g' % absent))
            if net < -0.005 and slip._ksw_shows_full_deductions():
                # By design: a vacation / EOS settlement lists the whole
                # obligation and collects only what the pay covers.
                detail += self._para(_(
                    'A vacation or End of Service settlement lists every '
                    'outstanding deduction, so its NET can be negative. '
                    'Only what the pay covers is collected; the rest stays '
                    'pending.'))
                out.append(self._finding(
                    'neg_net', 'info', slip,
                    _('Settlement deductions exceed the pay: %(net)s',
                      net=self._money(ctx, net)),
                    detail, net))
            elif net < -0.005:
                detail += self._para(_(
                    'A negative NET is left out of the bank file: the '
                    'deductions are larger than the salary.'))
                out.append(self._finding(
                    'neg_net', 'critical', slip,
                    _('Negative NET: %(net)s', net=self._money(ctx, net)),
                    detail, net))
            elif gross > 0.005:
                worked = wd.get('WORK100', {}).get('number_of_days', 0.0)
                if worked > 0.005:
                    detail += self._para(_(
                        'The employee worked %(days)s days, yet nothing will '
                        'be paid: the deductions above take the whole '
                        'salary.', days='%g' % worked))
                    out.append(self._finding(
                        'zero_net', 'warning', slip,
                        _('Worked, but NET is zero'), detail))
                else:
                    detail += self._para(_(
                        'No worked days this period, so nothing is paid. '
                        'Expected if the employee did not work; otherwise '
                        'check the attendance first.'))
                    out.append(self._finding(
                        'zero_net', 'info', slip,
                        _('Whole period absent, NET is zero'), detail))
        return out

    # ------------------------------------------------------------------
    # 2. Same employee twice in this batch
    # ------------------------------------------------------------------
    def _check_duplicate_in_batch(self, ctx):
        if not ctx['run']:
            return []
        out = []
        by_emp = {}
        for slip in ctx['slips'].filtered(self._is_monthly):
            by_emp.setdefault(slip.employee_id, []).append(slip)
        for employee, slips in by_emp.items():
            if len(slips) < 2:
                continue
            rows = [(self._slip_ref(s), s.date_from, s.date_to,
                     dict(s._fields['state'].selection).get(s.state),
                     self._money(ctx, self._net(s))) for s in slips]
            extra = slips[1:]
            out.append(self._finding(
                'duplicate_in_batch', 'critical', extra[0],
                _('%(n)s payslips for the same employee in this batch',
                  n=len(slips)),
                self._html_table([_('Payslip'), _('From'), _('To'), _('Status'),
                             _('NET')], rows),
                sum(self._net(s) for s in extra)))
        return out

    # ------------------------------------------------------------------
    # 3. Period already paid / return not confirmed
    #    (the two rules of hr.payslip.run._ksw_superseded_slip_reasons,
    #    re-asked read-only: that method's override writes skip lines)
    # ------------------------------------------------------------------
    def _check_superseded(self, ctx):
        slips = ctx['draft'].filtered(self._is_monthly)
        if not slips:
            return []
        out = []
        employees = slips.employee_id
        date_to = max(slips.mapped('date_to'))
        date_from = min(slips.mapped('date_from'))
        done = self.env['hr.payslip'].sudo().search([
            ('employee_id', 'in', employees.ids),
            ('state', '=', 'done'),
            ('date_from', '<=', date_to),
            ('date_to', '>=', date_from),
        ]).filtered(lambda s: not s._is_settled_vacation_payslip())
        leaves = self.env['hr.leave'].sudo().search([
            ('employee_id', 'in', employees.ids),
            ('state', '=', 'validate'),
            ('x_return_state', '=', 'on_vacation'),
            ('request_date_from', '<=', date_to),
        ])
        for slip in slips:
            net = self._net(slip)
            frozen = self._frozen_note(slip)
            if frozen:
                consequence = _(
                    'The money already left in a bank file, so this month '
                    'is paid twice and must be recovered.')
            else:
                consequence = _(
                    'The batch removes this payslip by itself at the next '
                    'bank export or Mark as Done; nothing to do unless it '
                    'should be paid.')
            blocking = done.filtered(
                lambda d: d.employee_id == slip.employee_id
                and d.date_from <= slip.date_to
                and d.date_to >= slip.date_from)
            if blocking:
                rows = [(self._slip_ref(d),
                         d.x_leave_id.holiday_status_id.name
                         if d.x_leave_id else _('Payslip'),
                         d.date_from, d.date_to,
                         self._money(ctx, self._net(d))) for d in blocking]
                out.append(self._finding(
                    'paid_twice', 'critical', slip,
                    _('This period is already paid by %(slips)s',
                      slips=', '.join(self._slip_ref(d) for d in blocking)),
                    self._html_table([_('Confirmed payslip'), _('Type'),
                                 _('From'), _('To'), _('NET')], rows)
                    + frozen + self._para(consequence),
                    net))
                continue
            unresolved = leaves.filtered(
                lambda l: l.employee_id == slip.employee_id
                and l.request_date_from <= slip.date_to)
            if unresolved:
                rows = [(l.holiday_status_id.name, l.request_date_from,
                         l.request_date_to) for l in unresolved]
                out.append(self._finding(
                    'return_unconfirmed', 'warning', slip,
                    _('Return from leave not confirmed'),
                    self._html_table([_('Leave'), _('From'), _('To')], rows)
                    + frozen + self._para(consequence),
                    net))
        return out

    # ------------------------------------------------------------------
    # 4. Confirmed payslips: deductions vs installments
    #    (KSWCO SLIP 18441: settled installment missing from the payslip;
    #    the reverse is an installment the salary paid that is still
    #    pending and will be collected again)
    # ------------------------------------------------------------------
    @api.model
    def _ded_inputs(self, slip):
        """``{line_id: (input, collected)}`` for the slip's KSW_DED_ inputs."""
        out = {}
        for inp in slip.input_line_ids:
            code = inp.code or ''
            if code.startswith('KSW_DED_') and code[8:].isdigit():
                collected = (inp.amount or 0.0) - (inp.x_ksw_uncollected or 0.0)
                out[int(code[8:])] = (inp, collected)
        return out

    def _check_done_deductions(self, ctx):
        slips = ctx['done']
        if not slips:
            return []
        Line = self.env['ksw.deduction.line'].sudo()
        settled = Line.search([('payslip_id', 'in', slips.ids),
                               ('state', '=', 'paid')])
        settled_by_slip = {}
        for line in settled:
            settled_by_slip.setdefault(line.payslip_id.id, Line)
            settled_by_slip[line.payslip_id.id] |= line
        out = []
        for slip in slips:
            inputs = self._ded_inputs(slip)
            referenced = Line.browse(list(inputs)).exists()
            not_settled = []
            for line_id, (inp, collected) in inputs.items():
                if collected <= 0.005:
                    continue
                line = referenced.filtered(lambda l: l.id == line_id)
                if not line:
                    not_settled.append((inp.name, _('deleted'), collected))
                elif line.state != 'paid' or line.payslip_id != slip:
                    where = (self._slip_ref(line.payslip_id)
                             if line.payslip_id else '')
                    not_settled.append((
                        inp.name,
                        '%s %s' % (dict(line._fields['state'].selection)
                                   .get(line.state), where),
                        collected))
            if not_settled:
                out.append(self._finding(
                    'ded_not_settled', 'critical', slip,
                    _('Deducted from the salary, installment not settled'),
                    self._html_table(
                        [_('Input'), _('Installment status'),
                         _('Deducted')],
                        [(n, s, self._money(ctx, a))
                         for n, s, a in not_settled])
                    + self._para(_(
                        'The payslip took this money but the installment '
                        'does not say so: it will be collected again by '
                        'the next payroll run.')),
                    sum(a for _n, _s, a in not_settled)))
            missing = settled_by_slip.get(slip.id, Line).filtered(
                lambda l: l.id not in inputs)
            if missing:
                out.append(self._finding(
                    'ded_settled_missing', 'critical', slip,
                    _('Installment marked collected by this payslip, but '
                      'not on it'),
                    self._html_table(
                        [_('Deduction'), _('Installment'), _('Amount')],
                        [(l.deduction_id.display_name,
                          '%s/%s' % (l.sequence,
                                     l.deduction_id.installments),
                          self._money(ctx, l.amount)) for l in missing])
                    + self._para(_(
                        'The deduction record says this payslip collected '
                        'it, yet the payslip carries no such deduction and '
                        'its NET does not include it. Check which figure '
                        'the bank actually paid before correcting either '
                        'side.')),
                    sum(missing.mapped('amount'))))
        return out

    # ------------------------------------------------------------------
    # 5. Draft payslips: installments now due that the payslip lacks
    #    (a loan activated after the batch was generated: Mark as Done
    #    recomputes and silently lowers NET, batch 250)
    # ------------------------------------------------------------------
    def _check_pending_deductions(self, ctx):
        slips = ctx['draft'].filtered(lambda s: not s.x_is_revision)
        if not slips:
            return []
        Payslip = self.env['hr.payslip'].sudo()
        Line = self.env['ksw.deduction.line'].sudo()
        out = []
        for slip in slips:
            if not slip.line_ids:
                continue
            full = slip._ksw_shows_full_deductions()
            due = Line.search(Payslip._ksw_pending_lines_domain(
                slip.employee_id.ids, slip.date_to, full=full))
            present = self._ded_inputs(slip)
            missing = due.filtered(lambda l: l.id not in present)
            if not missing:
                continue
            total = sum(missing.mapped('amount'))
            out.append(self._finding(
                'ded_pending_missing', 'warning', slip,
                _('%(n)s due installment(s) not on the payslip',
                  n=len(missing)),
                self._html_table(
                    [_('Deduction'), _('Installment'), _('Due'),
                     _('Amount')],
                    [(l.deduction_id.display_name,
                      '%s/%s' % (l.sequence, l.deduction_id.installments),
                      l.period_date, self._money(ctx, l.amount))
                     for l in missing])
                + self._frozen_note(slip)
                + self._para(_(
                    'They became due after this payslip was computed. '
                    'Mark as Done recomputes and will deduct them, '
                    'lowering NET by up to %(amount)s. A bank file '
                    'exported before that would not match.',
                    amount=self._money(ctx, total))),
                total, key_suffix=','.join(map(str, sorted(missing.ids)))))
        return out

    # ------------------------------------------------------------------
    # 6. Attendance changed after the worked days were built
    #    (Recompute never rebuilds worked days: pitfall #249, batch 263)
    # ------------------------------------------------------------------
    def _stale_candidates(self, ctx):
        Payslip = self.env['hr.payslip'].sudo()
        def sheet_pending(slip):
            # The unconfirmed-sheet check reports these. "From attendance
            # today" would read an unconfirmed sheet as a month absent,
            # and regenerating on it would zero the payslip.
            if slip.id not in ctx['sheet_state']:
                return False
            sheet = ctx['sheet_state'][slip.id]
            return not sheet or sheet.state != 'confirmed'

        return ctx['draft'].filtered(
            lambda s: self._is_monthly(s)
            and not sheet_pending(s)
            and s.worked_days_line_ids
            and not (s.struct_id and s.struct_id.x_absence_only_deduction)
            and not Payslip._get_vacation_return_date(s))

    def _check_stale_attendance(self, ctx):
        slips = self._stale_candidates(ctx)
        if not slips:
            return []
        stored = {s.id: self._worked_days(s) for s in slips}
        expected = {}
        Payslip = self.env['hr.payslip'].sudo()
        self.env.flush_all()
        try:
            with self.env.cr.savepoint():
                for slip in slips:
                    version_ids = slip.version_id.ids or Payslip.get_versions(
                        slip.employee_id, slip.date_from, slip.date_to)
                    if not version_ids:
                        continue
                    try:
                        vals = Payslip.get_worked_day_lines(
                            self.env['hr.version'].sudo().browse(version_ids),
                            slip.date_from, slip.date_to)
                    except Exception:  # noqa: BLE001 - a check, not a gate
                        _logger.warning(
                            'Payroll analyst: could not rebuild worked days '
                            'for payslip %s', slip.id, exc_info=True)
                        continue
                    agg = {}
                    for v in vals:
                        entry = agg.setdefault(v.get('code'), {
                            'name': v.get('name'),
                            'number_of_days': 0.0, 'amount': 0.0})
                        entry['number_of_days'] += v.get('number_of_days') or 0.0
                        entry['amount'] += v.get('amount') or 0.0
                    expected[slip.id] = agg
                raise _Rollback()
        except _Rollback:
            pass
        self.env.invalidate_all()

        out = []
        for slip in slips:
            if slip.id not in expected:
                continue
            now, then = expected[slip.id], stored[slip.id]
            rows = []
            for code, (fname, tol) in _WD_COMPARED.items():
                old = then.get(code, {}).get(fname, 0.0)
                new = now.get(code, {}).get(fname, 0.0)
                if abs(new - old) >= tol:
                    label = (then.get(code, {}).get('name')
                             or now.get(code, {}).get('name') or code)
                    fmt = (lambda v: self._money(ctx, v)) \
                        if fname == 'amount' else (lambda v: '%g' % v)
                    rows.append((label, fmt(old), fmt(new)))
            if not rows:
                continue
            delta = (now.get('ATT_DED', {}).get('amount', 0.0)
                     - then.get('ATT_DED', {}).get('amount', 0.0))
            out.append(self._finding(
                'stale_attendance', 'warning', slip,
                _('Worked days out of date'),
                self._html_table([_('Figure'), _('On the payslip'),
                             _('From attendance today')], rows)
                + self._frozen_note(slip)
                + self._para(_(
                    'Attendance, or how it is counted, changed after this '
                    'payslip was generated. Compute Sheet does not rebuild '
                    'worked days, so it still pays the old figures. Correct it by '
                    'regenerating the payslip, or by a revision once it is '
                    'paid.')),
                -delta))
        return out

    # ------------------------------------------------------------------
    # 7. Attendance-sheet employee whose sheet is not confirmed
    # ------------------------------------------------------------------
    def _check_sheet_unconfirmed(self, ctx):
        out = []
        for slip in ctx['draft'].filtered(
                lambda s: s.id in ctx['sheet_state']):
            sheet = ctx['sheet_state'][slip.id]
            if sheet and sheet.state == 'confirmed':
                continue
            absent = self._worked_days(slip).get(
                'ATT_ABS', {}).get('number_of_days', 0.0)
            if sheet:
                detail = _('The attendance sheet for %(month)s/%(year)s is '
                           'still a draft (supervisor: %(sup)s).',
                           month=sheet.month, year=sheet.year,
                           sup=sheet.manager_id.name or '-')
            else:
                detail = _('There is no attendance sheet for %(month)s/'
                           '%(year)s.', month=slip.date_from.month,
                           year=slip.date_from.year)
            out.append(self._finding(
                'sheet_unconfirmed', 'warning', slip,
                _('Attendance sheet not confirmed'),
                self._para(detail)
                + self._para(_(
                    'The payslip counts %(days)s absent days. Confirming '
                    'the sheet later does not update it (Compute Sheet '
                    'keeps the worked days).', days='%g' % absent))))
        return out

    # ------------------------------------------------------------------
    # 8. NET moved a lot since last month, with the lines that moved it
    # ------------------------------------------------------------------
    @api.model
    def _line_deltas(self, slip, prev):
        """``[(name, before, now, delta)]`` sorted by size, totals excluded."""
        now, before = self._totals(slip), self._totals(prev)
        rows = []
        for code in set(now) | set(before):
            if code in _TOTAL_CODES:
                continue
            n_name, n_val = now.get(code, (None, 0.0))
            b_name, b_val = before.get(code, (None, 0.0))
            if abs(n_val - b_val) < 1:
                continue
            rows.append((n_name or b_name, b_val, n_val, n_val - b_val))
        rows.sort(key=lambda r: -abs(r[3]))
        return rows

    @api.model
    def _wd_deltas(self, slip, prev):
        now, before = self._worked_days(slip), self._worked_days(prev)
        rows = []
        for code in ('WORK100', 'ATT_ABS', 'ATT_LATE', 'ATT_EARLY'):
            n = now.get(code, {}).get('number_of_days', 0.0)
            b = before.get(code, {}).get('number_of_days', 0.0)
            if abs(n - b) >= 0.5:
                name = (now.get(code) or before.get(code))['name']
                rows.append((name, b, n))
        return rows

    def _check_net_swing(self, ctx):
        slips = ctx['slips'].filtered(self._is_monthly)
        out = []
        for slip in slips:
            prev = self._previous_for(ctx, slip)
            if not prev or not slip.line_ids:
                continue
            net, prev_net = self._net(slip), self._net(prev)
            change = net - prev_net
            if abs(change) < SWING_MIN_AMOUNT or (
                    prev_net > 0 and abs(change) < SWING_RATIO * prev_net):
                continue
            if net <= 0.005:
                continue  # already reported by the NET check
            lines = self._line_deltas(slip, prev)
            detail = self._para(_(
                'NET %(now)s against %(before)s on %(prev)s (%(sign)s%(chg)s).',
                now=self._money(ctx, net), before=self._money(ctx, prev_net),
                prev=self._slip_ref(prev), sign='+' if change > 0 else '',
                chg=self._money(ctx, change)))
            if lines:
                detail += self._html_table(
                    [_('Line'), _('Last month'), _('This month'),
                     _('Change')],
                    [(n, self._money(ctx, b), self._money(ctx, v),
                      self._money(ctx, d)) for n, b, v, d in lines])
            wd = self._wd_deltas(slip, prev)
            if wd:
                detail += self._html_table(
                    [_('Days'), _('Last month'), _('This month')],
                    [(n, '%g' % b, '%g' % v) for n, b, v in wd])
            out.append(self._finding(
                'net_swing', 'info' if change > 0 else 'warning', slip,
                _('NET changed by %(chg)s since last month',
                  chg=self._money(ctx, change)),
                detail, change))
        return out

    # ------------------------------------------------------------------
    # 9. Payable NET with no bank account
    # ------------------------------------------------------------------
    def _check_no_bank(self, ctx):
        out = []
        for slip in ctx['slips']:
            net = self._net(slip)
            if net <= 0.005 or slip.x_paid_outside_bank_file:
                continue
            if slip.x_paid_bank_account_id or \
                    slip.employee_id.sudo().primary_bank_account_id:
                continue
            out.append(self._finding(
                'no_bank', 'warning', slip, _('No bank account'),
                self._para(_(
                    'NET %(net)s, but the employee has no salary bank '
                    'account. The Kawthar file leaves the payslip out; the '
                    'WPS file writes it without an IBAN.',
                    net=self._money(ctx, net))), net))
        return out

    # ------------------------------------------------------------------
    # 10. Paid last month, no payslip at all for this period
    # ------------------------------------------------------------------
    def _check_missing_employee(self, ctx):
        run = ctx['run']
        if not run or not run.date_end:
            return []
        since = run.date_start - relativedelta(months=1)
        previous = {}
        for emp_id, prevs in ctx['previous_pool'].items():
            for prev in prevs:
                if prev.date_to < run.date_start and prev.date_from >= since:
                    previous[emp_id] = prev
                    break
        if not previous:
            return []
        Payslip = self.env['hr.payslip'].sudo()
        have = set(Payslip.search([
            ('state', '!=', 'cancel'),
            ('date_from', '<=', run.date_end),
            ('date_to', '>=', run.date_start),
        ]).employee_id.ids)
        candidates = self.env['hr.employee'].sudo().browse(
            [e for e in previous if e not in have]).exists()
        out = []
        for employee in candidates:
            if not employee.active or employee.x_exclude_from_payroll:
                continue
            version = employee.current_version_id
            end = version.contract_date_end if version else False
            if not version or (end and end < run.date_start):
                continue
            prev = previous[employee.id]
            out.append({
                'check_code': 'missing_employee',
                'severity': 'info',
                'slip_id': False,
                'employee_id': employee.id,
                'title': _('Paid last month, no payslip for this period'),
                'detail': self._para(_(
                    'Last paid by %(slip)s (%(net)s). No payslip for this '
                    'period in any batch, and the contract is still '
                    'running.', slip=self._slip_ref(prev),
                    net=self._money(ctx, self._net(prev)))),
                'amount': 0.0,
                'key': 'missing_employee:%s' % employee.id,
            })
        return out

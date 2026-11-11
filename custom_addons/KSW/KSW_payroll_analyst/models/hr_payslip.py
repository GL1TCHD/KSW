from markupsafe import Markup

from odoo import api, fields, models, _
from odoo.exceptions import UserError
from odoo.tools.misc import formatLang

from odoo.addons.KSW_payroll.models.hr_payslip import _WD_UNITS


class HrPayslip(models.Model):
    _inherit = 'hr.payslip'

    x_finding_ids = fields.One2many(
        'ksw.payroll.finding', 'slip_id', string='Payroll Check Findings')
    # compute_sudo + no model groups=: referenced by the banner's
    # invisible=, which the read-only tiers parse too (pitfall #31).
    x_open_finding_count = fields.Integer(
        string='Open Findings', compute='_compute_open_finding_count', compute_sudo=True)

    # Built only from what the payslip already stores, in the reader's
    # language. compute_sudo because the read-only tiers (Only Self,
    # Supervisor) can read the payslip but not every model behind it
    # (inputs, the previous payslip's lines); it only ever describes this
    # payslip and the same employee's previous one.
    x_explanation_html = fields.Html(
        string='Explanation', compute='_compute_explanation_html',
        compute_sudo=True, sanitize=False)

    def action_ksw_check_payroll(self):
        """Check the selected payslips on their own (no batch).

        Replaces the stand-alone findings of these payslips; findings
        that came from checking their batch are left to the batch.
        """
        if not self.env.su and not self.env.user.has_group(
                'om_hr_payroll.group_hr_payroll_user'):
            raise UserError(_('Only a Payroll Officer may check payslips.'))
        self._ksw_check_standalone()
        action = self.env['ir.actions.actions']._for_xml_id(
            'KSW_payroll_analyst.action_ksw_payroll_finding')
        action['domain'] = [('slip_id', 'in', self.ids),
                            ('run_id', '=', False)]
        action['context'] = {'search_default_open': 1}
        return action

    def _ksw_check_standalone(self):
        Finding = self.env['ksw.payroll.finding'].sudo()
        previous = {
            f.key: f for f in Finding.search([
                ('slip_id', 'in', self.ids), ('run_id', '=', False),
                ('state', '=', 'reviewed')])
        }
        vals_list = self.env['ksw.payroll.analyst']._analyze_slips(self)
        self.env['hr.payslip.run']._ksw_carry_reviews(vals_list, previous)
        Finding.search([('slip_id', 'in', self.ids),
                        ('run_id', '=', False)]).unlink()
        Finding.create(vals_list)

    @api.depends('x_finding_ids.state')
    def _compute_open_finding_count(self):
        for slip in self:
            slip.x_open_finding_count = len(
                slip.x_finding_ids.filtered(lambda f: f.state == 'open'))

    @api.depends_context('lang')
    @api.depends('line_ids.total', 'worked_days_line_ids.number_of_days',
                 'input_line_ids.amount', 'state')
    def _compute_explanation_html(self):
        Analyst = self.env['ksw.payroll.analyst']
        currency = self.env.company.currency_id
        for slip in self:
            if not slip.line_ids:
                slip.x_explanation_html = Analyst._para(_(
                    'This payslip has not been computed yet.'))
                continue
            slip.x_explanation_html = slip._ksw_explain(Analyst, currency)

    def _ksw_explain(self, Analyst, currency):
        self.ensure_one()

        def money(v):
            return formatLang(self.env, v, currency_obj=currency)

        def h(text):
            return Markup('<h5 class="mt-3">%s</h5>') % text

        totals = Analyst._totals(self)
        net = totals.get('NET', ('', 0.0))[1]
        gross = totals.get('GROSS', ('', 0.0))[1]
        earnings = [(n, t) for c, (n, t) in totals.items()
                    if t > 0 and c not in ('GROSS', 'NET')]
        deductions = sorted(
            ((c, n, t) for c, (n, t) in totals.items()
             if t < 0 and c not in ('GROSS', 'NET')),
            key=lambda r: r[2])
        out = Markup('')

        # -- Headline --------------------------------------------------
        paid_out = sum(t for _n, t in earnings) + sum(
            t for _c, _n, t in deductions)
        if abs(paid_out - net) < 1:
            out += Analyst._para(Markup('<strong>%s</strong>') % _(
                'Net salary %(net)s = earnings %(earn)s − deductions '
                '%(ded)s.', net=money(net),
                earn=money(sum(t for _n, t in earnings)),
                ded=money(-sum(t for _c, _n, t in deductions))))
        else:
            out += Analyst._para(Markup('<strong>%s</strong>') % _(
                'Net salary %(net)s (gross %(gross)s).',
                net=money(net), gross=money(gross)))
        if self.x_is_revision and self.x_revised_payslip_id:
            out += Analyst._para(_(
                'This is a revision of %(slip)s: the whole period is '
                'recalculated with current data, and what was already paid '
                'is subtracted, so the NET is only the difference.',
                slip=Analyst._slip_ref(self.x_revised_payslip_id)))
        elif self.x_leave_id:
            leave = self.x_leave_id
            out += Analyst._para(_(
                'Leave settlement for %(type)s, %(start)s to %(end)s.',
                type=leave.holiday_status_id.name,
                start=leave.request_date_from, end=leave.request_date_to))

        # -- Earnings ----------------------------------------------------
        if earnings:
            out += h(_('Earnings'))
            out += Analyst._html_table(
                [_('Item'), _('Amount')],
                [(n, money(t)) for n, t in earnings])

        # -- Deductions, each with its reason ---------------------------
        if deductions:
            out += h(_('Deductions'))
            out += Analyst._html_table(
                [_('Item'), _('Amount')],
                [(n, money(-t)) for _c, n, t in deductions])
            codes = {c for c, _n, _t in deductions}
            if 'ATTDED' in codes:
                out += self._ksw_explain_attendance(Analyst)
            if 'KSW_DEDUCTIONS' in codes:
                out += self._ksw_explain_installments(Analyst, money)

        # -- Against last month -----------------------------------------
        if Analyst._is_monthly(self):
            prev = Analyst._previous_slips(
                self.employee_id, self.date_from).get(self.employee_id.id)
            if prev and prev != self:
                out += self._ksw_explain_change(Analyst, prev, net, money)
        return out

    def _ksw_explain_attendance(self, Analyst):
        rows = []
        for wd in self.worked_days_line_ids:
            if wd.code in ('WORK100', 'ATT_DED') or wd.code not in _WD_UNITS:
                continue
            count_unit, value_unit = _WD_UNITS[wd.code]
            if not wd.number_of_days and not wd.number_of_hours:
                continue
            count = '%g' % (wd.number_of_days or 0.0)
            if count_unit == 'days':
                count = _('%(n)s days', n=count)
            elif count_unit == 'times':
                count = _('%(n)s times', n=count)
            hours = (_('%(n)s hours', n='%.2f' % wd.number_of_hours)
                     if value_unit and wd.number_of_hours else '')
            rows.append((wd.name, count, hours))
        if not rows:
            return Markup('')
        return (Analyst._para(Markup('<em>%s</em>') % _(
            'The attendance deduction comes from:'))
            + Analyst._html_table([_('Attendance'), _('Count'), _('Time')], rows))

    def _ksw_explain_installments(self, Analyst, money):
        rows, uncollected = [], 0.0
        for inp in self.input_line_ids:
            code = inp.code or ''
            if code.startswith('KSW_DEDP_'):
                rows.append((inp.name, money(inp.amount),
                             _('already collected earlier')))
            elif code.startswith('KSW_DED_'):
                left = inp.x_ksw_uncollected or 0.0
                collected = (inp.amount or 0.0) - left
                note = ''
                if left > 0.005:
                    uncollected += left
                    note = _('%(left)s not collected, still owed',
                             left=money(left))
                rows.append((inp.name, money(collected), note))
        if not rows:
            return Markup('')
        out = Analyst._para(Markup('<em>%s</em>') % _(
            'Loans and deductions taken from this payslip:'))
        out += Analyst._html_table([_('Deduction'), _('Taken'), _('Note')], rows)
        if uncollected > 0.005:
            out += Analyst._para(_(
                'The salary could not cover %(amt)s of the installments; '
                'that part stays pending for a later payslip.',
                amt=money(uncollected)))
        return out

    def _ksw_explain_change(self, Analyst, prev, net, money):
        prev_net = Analyst._net(prev)
        change = net - prev_net
        out = Markup('<h5 class="mt-3">%s</h5>') % _(
            'Compared with last month')
        if abs(change) < 1:
            return out + Analyst._para(_(
                'Same NET as %(slip)s.', slip=Analyst._slip_ref(prev)))
        out += Analyst._para(_(
            'NET %(now)s against %(before)s on %(slip)s: %(dir)s of '
            '%(chg)s.', now=money(net), before=money(prev_net),
            slip=Analyst._slip_ref(prev),
            dir=_('an increase') if change > 0 else _('a decrease'),
            chg=money(abs(change))))
        lines = Analyst._line_deltas(self, prev)
        if lines:
            out += Analyst._html_table(
                [_('Line'), _('Last month'), _('This month'), _('Change')],
                [(n, money(b), money(v), money(d)) for n, b, v, d in lines])
        wd = Analyst._wd_deltas(self, prev)
        if wd:
            out += Analyst._html_table(
                [_('Days'), _('Last month'), _('This month')],
                [(n, '%g' % b, '%g' % v) for n, b, v in wd])
        return out

import logging

from dateutil.relativedelta import relativedelta

from markupsafe import Markup

from odoo import api, fields, models, _
from odoo.exceptions import UserError
from odoo.tools import float_compare

_logger = logging.getLogger(__name__)


class HrPayslipRun(models.Model):
    _inherit = 'hr.payslip.run'

    x_finding_ids = fields.One2many(
        'ksw.payroll.finding', 'run_id', string='Payroll Check Findings')
    x_analysis_date = fields.Datetime(
        string='Last Checked', readonly=True, copy=False)
    x_analysis_by = fields.Many2one(
        'res.users', string='Checked By', readonly=True, copy=False)
    # compute_sudo + no model groups=: read by the banner's invisible=
    # (pitfall #31); the findings themselves stay ACL-gated.
    x_finding_critical_count = fields.Integer(
        string='Critical Findings', compute='_compute_finding_counts', compute_sudo=True)
    x_finding_warning_count = fields.Integer(
        string='Warning Findings', compute='_compute_finding_counts', compute_sudo=True)
    x_finding_open_count = fields.Integer(
        string='Open Findings',
        compute='_compute_finding_counts', compute_sudo=True)

    @api.depends('x_finding_ids.state', 'x_finding_ids.severity')
    def _compute_finding_counts(self):
        for run in self:
            open_ = run.x_finding_ids.filtered(lambda f: f.state == 'open')
            run.x_finding_open_count = len(open_)
            run.x_finding_critical_count = len(
                open_.filtered(lambda f: f.severity == 'critical'))
            run.x_finding_warning_count = len(
                open_.filtered(lambda f: f.severity == 'warning'))

    # ------------------------------------------------------------------
    def _ksw_run_analysis(self):
        """Replace each batch's findings with a fresh check.

        Writes findings only. A finding an officer marked reviewed stays
        reviewed when it comes back with the same key and amount; if the
        amount moved, it is a different situation and reopens.
        """
        Finding = self.env['ksw.payroll.finding'].sudo()
        Analyst = self.env['ksw.payroll.analyst']
        for run in self:
            previous = {
                f.key: f for f in run.sudo().x_finding_ids
                if f.state == 'reviewed'
            }
            vals_list = Analyst._analyze(run)
            self._ksw_carry_reviews(vals_list, previous)
            run.sudo().x_finding_ids.unlink()
            Finding.create(vals_list)
            run.sudo().write({
                'x_analysis_date': fields.Datetime.now(),
                'x_analysis_by': self.env.uid,
            })

    @api.model
    def _ksw_carry_reviews(self, vals_list, previous):
        """Keep the review mark of a finding that came back unchanged."""
        for vals in vals_list:
            old = previous.get(vals['key'])
            if old and not float_compare(
                    old.amount, vals['amount'], precision_digits=2):
                vals.update({
                    'state': 'reviewed',
                    'reviewed_by': old.reviewed_by.id,
                    'reviewed_on': old.reviewed_on,
                    'review_note': old.review_note,
                })

    def action_ksw_check_payroll(self):
        if not self.env.su and not self.env.user.has_group(
                'om_hr_payroll.group_hr_payroll_user'):
            raise UserError(_('Only a Payroll Officer may check a batch.'))
        self._ksw_run_analysis()
        for run in self:
            run.sudo().message_post(
                body=Markup('<strong>%s</strong><br/>%s') % (
                    _('Payroll checked'),
                    _('%(c)s critical, %(w)s warnings, %(o)s open in total.',
                      c=run.x_finding_critical_count,
                      w=run.x_finding_warning_count,
                      o=run.x_finding_open_count)),
                subtype_xmlid='mail.mt_note')
        return self.action_ksw_open_findings()

    def action_ksw_open_findings(self):
        action = self.env['ir.actions.actions']._for_xml_id(
            'KSW_payroll_analyst.action_ksw_payroll_finding')
        action['domain'] = [('run_id', 'in', self.ids)]
        action['context'] = {'search_default_open': 1,
                             'search_default_group_check': 1}
        if len(self) == 1:
            action['name'] = _('Payroll Check: %(name)s', name=self.name)
        return action

    @api.model
    def _cron_ksw_analyze_draft_runs(self):
        runs = self.search([('state', '=', 'draft'),
                            ('slip_ids', '!=', False)])
        for run in runs:
            try:
                with self.env.cr.savepoint():
                    run._ksw_run_analysis()
            except Exception:  # noqa: BLE001 - one batch must not stop the rest
                _logger.exception('Payroll analyst failed on batch %s', run.id)
        # Vacation / EOS settlements live outside any batch and settle
        # every outstanding deduction: check the recent ones too.
        loose = self.env['hr.payslip'].search([
            ('payslip_run_id', '=', False),
            ('state', 'in', ('draft', 'done')),
            '|', ('x_leave_id', '!=', False), ('x_is_revision', '=', True),
            ('date_to', '>=', fields.Date.today() - relativedelta(days=62)),
        ])
        if loose:
            try:
                with self.env.cr.savepoint():
                    loose._ksw_check_standalone()
            except Exception:  # noqa: BLE001
                _logger.exception('Payroll analyst failed on loose payslips')

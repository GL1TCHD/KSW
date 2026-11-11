from odoo import api, fields, models, _
from odoo.exceptions import UserError

SEVERITIES = [
    ('critical', 'Critical'),
    ('warning', 'Warning'),
    ('info', 'Info'),
]
SEVERITY_RANK = {'critical': 1, 'warning': 2, 'info': 3}

CHECKS = [
    ('neg_net', 'Negative NET'),
    ('zero_net', 'Zero NET'),
    ('duplicate_in_batch', 'Employee twice in the batch'),
    ('paid_twice', 'Period already paid'),
    ('return_unconfirmed', 'Return not confirmed'),
    ('ded_not_settled', 'Deducted, installment still pending'),
    ('ded_settled_missing', 'Installment settled, not on the payslip'),
    ('ded_pending_missing', 'Due installment not on the payslip'),
    ('stale_attendance', 'Worked days out of date'),
    ('sheet_unconfirmed', 'Attendance sheet not confirmed'),
    ('net_swing', 'Large change from last month'),
    ('no_bank', 'No bank account'),
    ('missing_employee', 'Paid last month, not in this period'),
]

# Fields an officer may change on a finding; everything else is written
# by the analyst only (under sudo), never by hand.
_REVIEW_FIELDS = {'review_note'}


class KswPayrollFinding(models.Model):
    """One thing the payroll analyst wants a human to look at.

    Findings are throw-away output: every check replaces the batch's
    findings. The review mark is the only human input and is carried over
    to the next check when the same finding comes back unchanged
    (same ``key`` and same ``amount``).
    """
    _name = 'ksw.payroll.finding'
    _description = 'Payroll Analyst Finding'
    _order = 'severity_rank, check_code, employee_id, id'

    run_id = fields.Many2one(
        'hr.payslip.run', string='Batch', index=True,
        ondelete='cascade', readonly=True,
        help='Empty for a payslip checked on its own (a vacation or End '
             'of Service settlement outside any batch).')
    slip_id = fields.Many2one(
        'hr.payslip', string='Payslip', index=True, ondelete='cascade',
        readonly=True)
    employee_id = fields.Many2one(
        'hr.employee', string='Employee', readonly=True, ondelete='cascade')
    check_code = fields.Selection(CHECKS, string='Check', required=True,
                                  readonly=True)
    severity = fields.Selection(SEVERITIES, required=True, readonly=True)
    severity_rank = fields.Integer(readonly=True)
    title = fields.Char(required=True, readonly=True)
    detail = fields.Html(sanitize=True, readonly=True)
    amount = fields.Float(
        string='Amount at Stake', digits=(16, 2), readonly=True,
        help='The money this finding is about: the NET, the installment, '
             'or the change in the attendance deduction.')
    key = fields.Char(readonly=True, index=True,
                      help='Identifies the same finding across checks.')
    state = fields.Selection(
        [('open', 'Open'), ('reviewed', 'Reviewed')],
        default='open', required=True, readonly=True)
    reviewed_by = fields.Many2one('res.users', readonly=True)
    reviewed_on = fields.Datetime(readonly=True)
    review_note = fields.Char(string='Review Note')

    @api.model
    def _check_officer(self):
        if not self.env.su and not self.env.user.has_group(
                'om_hr_payroll.group_hr_payroll_user'):
            raise UserError(_('Only a Payroll Officer may review payroll '
                              'findings.'))

    def write(self, vals):
        # RPC can write any field the ACL allows: only the review note is
        # an officer's to change (pitfall #15 / #265).
        if not self.env.su and set(vals) - _REVIEW_FIELDS:
            raise UserError(_('Payroll findings are produced by the '
                              'analyst and cannot be edited.'))
        if not self.env.su:
            self._check_officer()
        return super().write(vals)

    def action_mark_reviewed(self):
        self._check_officer()
        self.sudo().write({
            'state': 'reviewed',
            'reviewed_by': self.env.uid,
            'reviewed_on': fields.Datetime.now(),
        })

    def action_reopen(self):
        self._check_officer()
        self.sudo().write({
            'state': 'open', 'reviewed_by': False, 'reviewed_on': False,
        })

    def action_open_slip(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'hr.payslip',
            'res_id': self.slip_id.id,
            'view_mode': 'form',
            'target': 'current',
        }

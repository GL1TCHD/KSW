from odoo import api, fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    # Lives here rather than in KSW_payroll: the payslip-revision feature
    # is in KSW_payroll, but `ksw.deduction.type` is defined by this module,
    # which depends on it — a Many2one cannot point the other way.
    ksw_overpay_recovery_type_id = fields.Many2one(
        'ksw.deduction.type',
        string='Over-payment Recovery Type',
        config_parameter='ksw_payroll.overpay_recovery_type_id',
        default=lambda self: self.env.ref(
            'KSW_deduction.type_advance', raise_if_not_found=False),
        help='Deduction type used when a payslip revision shows the '
             'employee was over-paid. Confirming such a revision cancels '
             'it and opens a draft deduction of this type for the '
             'difference, so the amount is recovered from a later payroll '
             'run instead of a negative net being paid.',
    )

    # Not `config_parameter=`: for a Boolean, unticking deletes the
    # parameter, and a missing parameter has to read as "on" here.
    ksw_notify_dm_non_loan = fields.Boolean(
        string='Notify Direct Manager of Deductions',
        help='When a deduction other than a personal loan is applied to an '
             'employee, send a notification to the employee\'s direct '
             'manager. Applies to every employee.',
    )

    @api.model
    def get_values(self):
        res = super().get_values()
        res['ksw_notify_dm_non_loan'] = (
            self.env['ksw.deduction']._dm_non_loan_notify_enabled())
        return res

    def set_values(self):
        super().set_values()
        self.env['ir.config_parameter'].sudo().set_param(
            self.env['ksw.deduction']._DM_NOTIFY_PARAM,
            'True' if self.ksw_notify_dm_non_loan else 'False')

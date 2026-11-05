"""Release payslips from the bank freeze (models/payslip_bank_freeze.py).

The freeze assumes a payslip in an exported text file was paid. When it
was not (the file was never uploaded, the bank rejected those rows, the
employee should not have been in the batch), a Payroll Manager releases
them here, with a reason. A released payslip can be recomputed or
deleted like any draft; the next text export freezes it again.
"""
from markupsafe import Markup

from odoo import api, fields, models, _
from odoo.exceptions import UserError

from odoo.addons.KSW_payroll.models.payslip_bank_freeze import _check_manager


class KswPayslipBankReleaseWizard(models.TransientModel):
    _name = 'ksw.payslip.bank.release.wizard'
    _description = 'Release Payslips from the Bank Freeze'

    slip_ids = fields.Many2many(
        'hr.payslip', string='Payslips', required=True,
        domain="[('state', '=', 'draft'), ('x_bank_exported_date', '!=', False)]")
    reason = fields.Text(required=True)
    confirm_not_paid = fields.Boolean(
        string='The bank did not pay these payslips')

    @api.model
    def default_get(self, fields_list):
        vals = super().default_get(fields_list)
        ctx = self.env.context
        slips = self.env['hr.payslip']
        if ctx.get('active_model') == 'hr.payslip.run':
            slips = self.env['hr.payslip.run'].browse(
                ctx.get('active_ids') or ctx.get('active_id')).slip_ids
        elif ctx.get('active_model') == 'hr.payslip':
            slips = self.env['hr.payslip'].browse(
                ctx.get('active_ids') or ctx.get('active_id'))
        slips = slips.filtered(lambda s: s._ksw_is_bank_frozen())
        if slips and 'slip_ids' in fields_list:
            vals['slip_ids'] = [(6, 0, slips.ids)]
        return vals

    def action_release(self):
        self.ensure_one()
        # Reachable over RPC like any model: authority is checked here,
        # not only on the button.
        _check_manager(self.env, _('release payslips from the bank freeze'))
        reason = (self.reason or '').strip()
        if not reason:
            raise UserError(_('A reason is required.'))
        if not self.confirm_not_paid:
            raise UserError(_(
                'Confirm that the bank did not pay these payslips. If it '
                'did, keep them frozen: deleting or recomputing a paid '
                'payslip makes Odoo disagree with the bank.'))
        slips = self.slip_ids.sudo().filtered(lambda s: s._ksw_is_bank_frozen())
        if not slips:
            raise UserError(_('None of these payslips is frozen.'))
        pending = slips.x_pending_change_ids.filtered(
            lambda p: p.state == 'pending')
        if pending:
            pending._close('dismissed', _(
                'Payslip released from the bank freeze; recompute it to '
                'apply the change.'))
        slips.with_context(_ksw_skip_bank_refresh=True).write({
            'x_bank_exported_date': False, 'x_bank_exported_by': False})
        body_tpl = Markup(
            '<strong>&#128275; %(title)s</strong><br/>'
            '<b>By:</b> %(user)s<br/><b>Reason:</b> %(reason)s')
        for slip in slips:
            slip.message_post(body=body_tpl % {
                'title': _('Released from the bank freeze'),
                'user': self.env.user.name, 'reason': reason,
            }, subtype_xmlid='mail.mt_note')
        for run in slips.payslip_run_id:
            mine = slips.filtered(lambda s: s.payslip_run_id == run)
            run.message_post(body=Markup(
                '<strong>&#128275; %(title)s</strong><br/>'
                '<b>By:</b> %(user)s<br/><b>Reason:</b> %(reason)s'
                '<ul>%(rows)s</ul>%(note)s'
            ) % {
                'title': _('%(n)s payslip(s) released from the bank freeze',
                           n=len(mine)),
                'user': self.env.user.name, 'reason': reason,
                'rows': Markup('').join(
                    Markup('<li>%(emp)s</li>') % {'emp': s.employee_id.name}
                    for s in mine),
                'note': _('They can now be recomputed or deleted. Any bank '
                          'file already exported for them is out of date.'),
            }, subtype_xmlid='mail.mt_note')
        return {'type': 'ir.actions.act_window_close'}

from odoo import _, api, fields, models
from odoo.exceptions import UserError

SIGNATURE_METHOD_SELECTION = [
    ('wacom_pad', 'Wacom Signature Pad'),
    ('manual', 'Mouse / Touch'),
]


class ItAssetAssignment(models.Model):
    _inherit = 'it.asset.assignment'

    # Captured with KSW_wacom_signature's widget (Wacom pad, or mouse/touch).
    employee_signature = fields.Binary(string='Employee Signature', attachment=True, copy=False)
    employee_signature_method = fields.Selection(
        SIGNATURE_METHOD_SELECTION, string='Employee Signed Via', readonly=True, copy=False)
    employee_signed_date = fields.Datetime(string='Employee Signed On', readonly=True, copy=False)
    it_signature = fields.Binary(string='IT Signature', attachment=True, copy=False)
    it_signature_method = fields.Selection(
        SIGNATURE_METHOD_SELECTION, string='IT Signed Via', readonly=True, copy=False)
    it_signed_date = fields.Datetime(string='IT Signed On', readonly=True, copy=False)
    is_fully_signed = fields.Boolean(compute='_compute_is_fully_signed')

    @api.depends('employee_signature', 'it_signature')
    def _compute_is_fully_signed(self):
        for rec in self:
            rec.is_fully_signed = bool(rec.employee_signature and rec.it_signature)

    @api.model_create_multi
    def create(self, vals_list):
        self._stamp_signature_dates(vals_list)
        return super().create(vals_list)

    def write(self, vals):
        self._stamp_signature_dates([vals])
        return super().write(vals)

    @api.model
    def _stamp_signature_dates(self, vals_list):
        now = fields.Datetime.now()
        for vals in vals_list:
            for sig, stamp in (('employee_signature', 'employee_signed_date'),
                               ('it_signature', 'it_signed_date')):
                if sig in vals:
                    vals[stamp] = now if vals[sig] else False

    def action_open_handover_form(self):
        """Open the saved assignment full-page, where it can be signed and printed."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Handover Form'),
            'res_model': 'it.asset.assignment',
            'res_id': self.id,
            'view_mode': 'form',
            'views': [(self.env.ref('KSW_helpdesk.it_asset_assignment_view_form').id, 'form')],
            'target': 'current',
        }

    def action_clear_signatures(self):
        if self.filtered(lambda r: r.state != 'active'):
            raise UserError(_('Signatures on a returned assignment cannot be changed.'))
        self.write({
            'employee_signature': False,
            'employee_signature_method': False,
            'it_signature': False,
            'it_signature_method': False,
        })
        return True

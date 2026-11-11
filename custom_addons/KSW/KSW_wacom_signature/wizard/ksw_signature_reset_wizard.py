from odoo import fields, models


class KswSignatureResetWizard(models.TransientModel):
    _name = 'ksw.signature.reset.wizard'
    _description = 'Reset Signed Document'

    document_id = fields.Many2one('ksw.signature.document', required=True, ondelete='cascade')
    reason = fields.Text(required=True)

    def action_confirm(self):
        self.ensure_one()
        self.document_id._reset_to_draft(self.reason)
        return {'type': 'ir.actions.act_window_close'}

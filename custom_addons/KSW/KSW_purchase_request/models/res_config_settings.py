from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    x_purchase_request_required = fields.Boolean(
        related='company_id.x_purchase_request_required', readonly=False)

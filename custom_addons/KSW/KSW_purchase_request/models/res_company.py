from odoo import fields, models


class ResCompany(models.Model):
    _inherit = 'res.company'

    x_purchase_request_required = fields.Boolean(
        'Require Purchase Request',
        help="A purchase order can only be confirmed when every product line "
             "comes from an approved purchase request (PR → RFQ → PO).")

    def write(self, vals):
        res = super().write(vals)
        if 'x_purchase_request_required' in vals:
            # load_menus is cached per user; the PR menus follow the setting.
            self.env.registry.clear_cache()
        return res

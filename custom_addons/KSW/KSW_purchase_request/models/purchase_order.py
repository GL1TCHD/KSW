from odoo import models
from odoo.exceptions import UserError


class PurchaseOrder(models.Model):
    _inherit = 'purchase.order'

    def button_confirm(self):
        self._ksw_check_purchase_request()
        return super().button_confirm()

    def _ksw_check_purchase_request(self):
        """PR step on: every product line must come from an approved request."""
        for order in self.filtered('company_id.x_purchase_request_required'):
            lines = order.order_line.filtered(lambda l: not l.display_type and l.product_id)
            missing = lines.filtered(lambda l: not l.purchase_request_lines.filtered(
                lambda r: r.request_state in ('approved', 'in_progress', 'done')))
            if missing:
                raise UserError(self.env._(
                    "%(order)s cannot be confirmed: a purchase request is required "
                    "for %(items)s. Create the RFQ from an approved purchase request "
                    "(Purchase Requests ▸ select the lines ▸ Create RFQ).",
                    order=order.name,
                    items=", ".join(missing.product_id.mapped('display_name'))))

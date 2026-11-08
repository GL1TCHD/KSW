from odoo import models

# Every menu purchase_request adds: its own app and the two entries under
# Purchase ▸ Orders. Hidden through the blacklist, not `active`: a
# <menuitem> reload on every upgrade of purchase_request sets active=True.
PR_MENUS = (
    'purchase_request.parent_menu_purchase_request',
    'purchase_request.menu_purchase_request_pro_mgt',
    'purchase_request.menu_purchase_request_line',
)


class IrUiMenu(models.Model):
    _inherit = 'ir.ui.menu'

    def _load_menus_blacklist(self):
        res = super()._load_menus_blacklist()
        if not self.env.company.x_purchase_request_required:
            for xmlid in PR_MENUS:
                menu = self.env.ref(xmlid, raise_if_not_found=False)
                if menu:
                    res += self.sudo().search([('id', 'child_of', menu.id)]).ids
        return res

from odoo import models
from odoo.http import request


class IrHttp(models.AbstractModel):
    _inherit = 'ir.http'

    def color_scheme(self):
        """Community always answers "light"; honour the per-device cookie the
        appearance switch sets (static/src/appearance/appearance.js)."""
        if request and request.httprequest.cookies.get('color_scheme') == 'dark':
            return 'dark'
        return super().color_scheme()

    def session_info(self):
        result = super().session_info()
        user = self.env.user
        if user._is_internal():
            result['ksw_tour_done'] = user.ksw_tour_done
        return result

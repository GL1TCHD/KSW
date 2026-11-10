import logging

from odoo import api, models

_logger = logging.getLogger(__name__)


class IrUiMenu(models.Model):
    _inherit = 'ir.ui.menu'

    @api.model
    def _ksw_set_menu_arabic(self, names):
        """Arabic labels for HR menus other modules own: ``{menu_xmlid: label}``.

        Writing in the ar_001 context changes only that language, and unlike
        a ``.po`` load it also corrects a label that is already set but wrong.
        An xmlid from a module that is not installed is skipped, not raised:
        ``hr_skills`` / ``hr_holidays_attendance`` are not dependencies.
        """
        if not self.env['res.lang']._lang_get('ar_001'):
            return True
        for xmlid, label in names.items():
            menu = self.env.ref(xmlid, raise_if_not_found=False)
            if not menu:
                _logger.info('Menu %s not installed; Arabic label skipped.', xmlid)
                continue
            menu.with_context(lang='ar_001').write({'name': label})
        return True

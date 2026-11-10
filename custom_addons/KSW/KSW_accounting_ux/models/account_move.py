from odoo import _, models


class AccountMove(models.Model):
    _inherit = 'account.move'

    def action_ksw_open_journal_items(self):
        """This entry's lines in the Journal Items list, where they can be
        searched, filtered and grouped (by account, partner, cost centre,
        employee…), which the one2many inside the form cannot do."""
        self.ensure_one()
        action = self.env['ir.actions.act_window']._for_xml_id('account.action_account_moves_all')
        action.update({
            'name': _('Journal Items · %s', self.name or self.ref or ''),
            'domain': [('move_id', '=', self.id),
                       ('display_type', 'not in', ('line_section', 'line_subsection', 'line_note'))],
            'context': {'journal_type': 'general'},
        })
        action.pop('path', None)
        return action

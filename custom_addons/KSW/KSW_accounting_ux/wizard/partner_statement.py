from odoo import api, fields, models

BOTH = 'all'


class StatementCommonWizard(models.AbstractModel):
    """Let the OCA partner statements be opened from a menu.

    Stock partner_statement only works from a partner list's Action menu: it
    reads the partners from ``active_ids``. Its Action entries are gated by
    two Settings toggles that, once on, show them to every internal user,
    while the wizard itself is billing-only. A partner field lets the
    Reporting menu open the dialog directly instead.
    """

    _inherit = 'statement.common.wizard'

    # Most KSW clients are also suppliers (and the reverse), so the default is
    # one statement netting both sides; the report half is in
    # report/partner_statement.py.
    account_type = fields.Selection(
        selection_add=[(BOTH, 'Receivable and Payable')],
        ondelete={BOTH: 'set default'}, default=BOTH)
    # Only the activity statements use it: the outstanding one has no start.
    x_date_range = fields.Selection(
        [('history', 'Full History'), ('year', 'Start of Year'),
         ('custom', 'Specific Range')],
        string='Period', default='year', required=True)

    partner_ids = fields.Many2many(
        'res.partner', string='Partners',
        default=lambda self: self._default_partner_ids())

    @api.model
    def _default_partner_ids(self):
        if self.env.context.get('active_model') == 'res.partner':
            return self.env.context.get('active_ids', [])
        return []

    @api.onchange('partner_ids')
    def _onchange_partner_ids_count(self):
        self.number_partner_ids = len(self.partner_ids)

    def _ksw_date_start(self, partner_ids):
        """The start date the chosen period stands for, as of ``date_end``."""
        if self.x_date_range == 'year':
            return self.company_id.compute_fiscalyear_dates(self.date_end)['date_from']
        # Full history starts at the partners' first posted entry, so the
        # title reads a real date and nothing is carried forward.
        self.env['account.move.line'].flush_model()
        self.env.cr.execute("""
            SELECT min(date) FROM account_move_line
             WHERE partner_id IN %s AND company_id = %s AND parent_state = 'posted'
        """, (tuple(partner_ids) or (0,), self.company_id.id))
        return min(self.env.cr.fetchone()[0] or self.date_end, self.date_end)

    @api.onchange('x_date_range', 'date_end', 'partner_ids')
    def _onchange_ksw_date_range(self):
        if 'date_start' in self._fields and self.x_date_range != 'custom' and self.date_end:
            self.date_start = self._ksw_date_start(self.partner_ids.ids)

    def _prepare_statement(self):
        if self.partner_ids:
            self = self.with_context(active_ids=self.partner_ids.ids)
        return super(StatementCommonWizard, self)._prepare_statement()


class ActivityStatementWizard(models.TransientModel):
    _inherit = 'activity.statement.wizard'

    @api.onchange('aging_type')
    def onchange_aging_type(self):
        # OCA moves the start date with the aging method; the period wins.
        res = super().onchange_aging_type()
        self._onchange_ksw_date_range()
        return res

    def _prepare_statement(self):
        # OCA adds date_start after its super(), so it is replaced here.
        res = super()._prepare_statement()
        if self.x_date_range != 'custom':
            res['date_start'] = self._ksw_date_start(res['partner_ids'])
        return res

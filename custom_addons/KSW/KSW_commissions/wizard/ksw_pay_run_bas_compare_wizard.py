"""Compare with BAS: which voucher to read the month against."""
from odoo import fields, models


class KswPayBasBaselineWizard(models.TransientModel):
    _inherit = 'ksw.pay.bas.baseline.wizard'

    def _read_bas(self):
        # The comparison has read the voucher already: match those rows.
        rows = self.env.context.get('ksw_bas_rows')
        return rows if rows else super()._read_bas()


class KswPayRunBasCompareWizard(models.TransientModel):
    _name = 'ksw.pay.run.bas.compare.wizard'
    _description = 'Compare a Commission Month with BAS'

    run_id = fields.Many2one('ksw.pay.run', required=True, readonly=True)
    voucher_number = fields.Char(
        string='BAS Voucher Number', required=True,
        help='The month\'s voucher in BAS, e.g. 26008282.')

    def action_compare(self):
        self.ensure_one()
        self.run_id._bas_compare((self.voucher_number or '').strip())
        return {'type': 'ir.actions.act_window_close'}

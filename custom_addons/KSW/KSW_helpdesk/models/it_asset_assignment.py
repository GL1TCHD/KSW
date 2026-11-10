from odoo import _, api, fields, models
from odoo.exceptions import UserError

CONDITION_SELECTION = [
    ('new', 'New'),
    ('good', 'Good'),
    ('fair', 'Fair'),
    ('poor', 'Poor'),
]


class ItAssetAssignment(models.Model):
    _name = 'it.asset.assignment'
    _description = 'IT Asset Assignment'
    _order = 'date_assigned desc, id desc'

    asset_id = fields.Many2one('it.asset', required=True, ondelete='cascade', index=True)
    category_id = fields.Many2one(related='asset_id.category_id', store=True, readonly=True)
    employee_id = fields.Many2one('hr.employee', required=True, index=True)
    assigned_by = fields.Many2one('res.users', default=lambda self: self.env.user, readonly=True)
    date_assigned = fields.Date(default=fields.Date.context_today, required=True)
    date_expected_return = fields.Date(
        string='Expected Return',
        help='Leave empty for a permanent assignment.',
    )
    date_returned = fields.Date()
    state = fields.Selection([
        ('active', 'Active'),
        ('returned', 'Returned'),
    ], default='active', required=True)
    condition_out = fields.Selection(
        CONDITION_SELECTION, string='Condition (Out)', default='good')
    condition_in = fields.Selection(CONDITION_SELECTION, string='Condition (Returned)')
    accessories = fields.Char(
        string='Accessories',
        help="Items handed over with the asset, e.g. 'Charger, bag, mouse'.",
    )
    notes = fields.Text()
    company_id = fields.Many2one(related='asset_id.company_id', store=True, readonly=True)

    def _compute_display_name(self):
        for rec in self:
            rec.display_name = f'{rec.asset_id.display_name} → {rec.employee_id.name or ""}'

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        # Saving an active assignment IS the assign action: the asset form's
        # "Assign to Employee" button opens this form directly, so the asset
        # must move to 'assigned' here rather than in a separate wizard.
        for rec in records.filtered(lambda r: r.state == 'active'):
            if rec.asset_id.state != 'available':
                raise UserError(_(
                    'Asset %s is not available. Return or repair it first.',
                    rec.asset_id.display_name,
                ))
            rec.asset_id.write({
                'state': 'assigned',
                'employee_id': rec.employee_id.id,
            })
        return records

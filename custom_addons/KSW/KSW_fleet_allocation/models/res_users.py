from odoo import api, fields, models


class ResUsers(models.Model):
    _inherit = 'res.users'

    # Who answers for which department's vehicles. Named on the user, not
    # derived from hr.department.manager_id: most vehicle departments have no
    # manager set, and the person raising requests is often an assistant.
    x_fleet_department_ids = fields.Many2many(
        'hr.department', 'ksw_fleet_user_department_rel', 'user_id', 'department_id',
        string='Vehicle Departments', domain=[('x_fleet_holder', '=', True)],
        groups='base.group_erp_manager',
        help='The departments whose vehicles this user may release and ask more for, '
             'and whose incoming transfers he confirms. Sub-departments included.')

    @api.model_create_multi
    def create(self, vals_list):
        users = super().create(vals_list)
        users.filtered('x_fleet_department_ids')._ksw_fleet_grant_user_role()
        return users

    x_fleet_role = fields.Selection([('user', 'Department User'), ('gm', 'General Manager')],
                                    string='Vehicle Allocation Role', compute='_compute_fleet_role',
                                    search='_search_fleet_role')

    def _compute_fleet_role(self):
        gm = self.env.ref('KSW_fleet_allocation.group_fleet_allocation_gm')
        user = self.env.ref('KSW_fleet_allocation.group_fleet_allocation_user')
        for record in self.sudo():
            groups = record.all_group_ids
            record.x_fleet_role = 'gm' if gm in groups else 'user' if user in groups else False

    def _search_fleet_role(self, operator, value):
        users = self.env.ref('KSW_fleet_allocation.group_fleet_allocation_user').sudo().all_user_ids
        if operator in ('!=', 'not in') and not value:
            return [('id', 'in', users.ids)]
        if operator in ('=', 'in') and not value:
            return [('id', 'not in', users.ids)]
        return NotImplemented

    def write(self, vals):
        res = super().write(vals)
        # Record rules read this list and their domains are cached per user.
        if 'x_fleet_department_ids' in vals:
            self.env.registry.clear_cache()
            self.filtered('x_fleet_department_ids')._ksw_fleet_grant_user_role()
        return res

    def _ksw_fleet_grant_user_role(self):
        """Naming a user's vehicle departments means he works with them: give
        him the Department User role too, or the app stays hidden from him
        (the menu is gated on the role, the departments are only the scope).
        Never removed here: a GM keeps his role with no departments named."""
        group = self.env.ref('KSW_fleet_allocation.group_fleet_allocation_user')
        missing = self.sudo().filtered(lambda u: group not in u.all_group_ids)
        if missing:
            missing.write({'group_ids': [(4, group.id)]})

    def _ksw_fleet_department_ids(self):
        """Ids of the departments this user answers vehicles for (with their
        sub-departments). Read by the record rules."""
        self.ensure_one()
        named = self.sudo().x_fleet_department_ids
        if not named:
            return []
        return self.env['hr.department'].sudo().with_context(active_test=False).search(
            [('id', 'child_of', named.ids)]).ids

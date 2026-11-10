from odoo import api, models


class KswFleetVehicle(models.Model):
    _inherit = 'ksw.fleet.vehicle'

    @api.model
    def _workshop_auto_scope(self, employee):
        """The workshop scope follows Vehicle Allocation. Without this, a
        truck transferred from Supply Chain to Disouky stayed bookable by
        Supply Chain (its old driver still on it) and not by Disouky."""
        allowed = super()._workshop_auto_scope(employee)
        employee = employee.sudo()
        Department = self.env['hr.department'].sudo().with_context(active_test=False)
        answers_for = employee.user_id._ksw_fleet_department_ids() if employee.user_id else []
        home = employee.department_id._ksw_fleet_holder() if employee.department_id else Department
        own = set(answers_for) | set(Department.search([('id', 'child_of', home.ids)]).ids if home else [])
        if not own:
            # Not part of any vehicle department (e.g. the maintenance
            # manager): the workshop's own rules stand unchanged.
            return allowed
        Vehicle = self.sudo()
        foreign = Vehicle.search([('id', 'in', list(allowed)), ('x_department_id', '!=', False),
                                  ('x_department_id', 'not in', list(own))])
        allowed -= set(foreign.ids)
        if answers_for:
            allowed |= set(Vehicle.search([('x_department_id', 'in', answers_for)]).ids)
        return allowed

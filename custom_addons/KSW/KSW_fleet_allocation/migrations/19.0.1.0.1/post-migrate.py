from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    # Users given vehicle departments before 19.0.1.0.1 never got the role,
    # so the app was hidden from them.
    env = api.Environment(cr, SUPERUSER_ID, {})
    env['res.users'].with_context(active_test=False).search(
        [('x_fleet_department_ids', '!=', False)])._ksw_fleet_grant_user_role()

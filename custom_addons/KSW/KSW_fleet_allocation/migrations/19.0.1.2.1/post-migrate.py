from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    # To-dos started in 19.0.1.2.0: give the GM and the receiving
    # departments the ones already owed by requests raised before.
    env = api.Environment(cr, SUPERUSER_ID, {})
    Request = env['ksw.fleet.request']
    open_requests = Request.search([('state', '=', 'submitted')])
    moving = env['ksw.fleet.move'].search([('state', '=', 'in_transfer')]).request_id
    (open_requests | moving)._ksw_fleet_sync_activities()

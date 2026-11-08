"""KSWCO inventory + purchase go-live, run once in odoo shell after the install.

    odoo shell -c /etc/odoo/odoo.conf -d <db> --no-http < kswco_golive.py
    (env KSW_COMMIT=1 to keep it; KSW_KEEPER=<login> assigns the count)

Decisions (Mohammed, 2026-11-08): Odoo runs the operations, BAS keeps the
books; the PR step is on; W65, W15 and W50 go first, at today's BAS
quantities. KSW_bas_gl_import is already installed on KSWCO (parallel run,
Nov 2026) and is left exactly as it is: its crons stay as they are.
"""
import os

DBS = ('KSWCO', 'kswco_rehearsal_inv', 'ksw_golive_sim')
assert env.cr.dbname in DBS, 'refusing to run on %s' % env.cr.dbname
COMMIT = os.environ.get('KSW_COMMIT') == '1'
KEEPER = os.environ.get('KSW_KEEPER')
STORES = '15,50,65'

company = env.company
print('company', company.name)

# 1. BAS keeps the books; the PR step is on.
company.write({'x_stock_books_in_bas': True, 'x_purchase_request_required': True})

# 2. Cost centres from BAS WREF10 (create-only), vehicles/branches as plans.
Gl = env['ksw.bas.gl.import']
print('cost centres', Gl.action_import_cost_centres(company))
Gl._ksw_split_cost_centre_plans()

# 3. The three stores, stock items only, then today's BAS quantities.
setup = env['ksw.bas.inventory.setup'].create({
    'company_id': company.id, 'warehouse_codes': STORES, 'stock_items_only': True})
setup.action_run_setup()
print(setup.log)
setup.action_load_opening_stock()
print(setup.log)

# 4. Count list for the keeper (Inventory > Physical Inventory).
quants = env['stock.quant'].search([
    ('location_id.warehouse_id.x_bas_code', 'in', STORES.split(',')),
    ('location_id.usage', '=', 'internal'), ('product_id.is_storable', '=', True)])
keeper = KEEPER and env['res.users'].search([('login', '=', KEEPER)], limit=1)
if keeper:
    quants.write({'user_id': keeper.id})
    print('count assigned to', keeper.login)
for wh in quants.location_id.warehouse_id:
    qs = quants.filtered(lambda q: q.location_id.warehouse_id == wh)
    print(wh.code, len(qs), 'items', sum(qs.mapped('quantity')), 'units')

entries = env['account.move'].search_count([('stock_move_ids', '!=', False)])
print('stock journal entries', entries)
if COMMIT:
    env.cr.commit(); print('COMMITTED')
else:
    env.cr.rollback(); print('ROLLED BACK (set KSW_COMMIT=1 to keep)')

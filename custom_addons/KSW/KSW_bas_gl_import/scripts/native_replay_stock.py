"""Tyres and RO membranes, Jan-Sep 2026, replayed through the native stock flows.

Runs after native_replay.py, on odoo_bas_native only. BAS keeps item lines for
purchases (din10, 006) and stock issues (bin10, 101); KSW_inventory's BAS setup
wizard reads both, and its readers are reused here.

* Opening, 31 Dec 2025: per item and store, BAS's quantity today minus every
  2026 purchase plus every 2026 issue (din10/bin10 hold the whole year), at the
  item's first 2026 BAS cost. Loaded without posting: the ledger's opening entry
  already carries the 1206 balances.
* A purchase: BAS's journal entry is removed and its user raises the purchase
  order, receives it into the store and posts the vendor bill.
* An issue: the journal entry is removed and its user validates a Stock Issue to
  consumption, charged to BAS's account, the vehicle as cost centre (the store's
  when BAS names none). Odoo values it at its own moving average.

    >>> exec(open('custom_addons/KSW/KSW_bas_gl_import/scripts/native_replay.py').read())
    >>> exec(open('custom_addons/KSW/KSW_bas_gl_import/scripts/native_replay_stock.py').read())
    >>> S = StockReplay(env); S.opening(); S.replay('2026-01-01', '2026-10-01')
"""
from collections import defaultdict
from datetime import datetime

from odoo.addons.KSW_inventory.wizard.bas_history_replay import _norm as _inv_norm

PREFIXES = ('31', '33')            # RO membranes, tyres
STORE_COST_CENTRE = {'15': '1200030001', '50': '120006', '65': '11120001'}
STOCK_GROUPS = ('purchase.group_purchase_user', 'stock.group_stock_user',
                'account.group_account_invoice')


class StockReplay(Replay):

    def __init__(self, env):
        super().__init__(env)
        self.load_bas_users()
        self.W = env['ksw.bas.inventory.setup'].sudo().create({'year': 2026})
        purchases, issues = self.W._bas_item_lines()
        keep = lambda r: r['ICODE'][:2] in PREFIXES
        self.purchases = [r for r in purchases if keep(r)]
        self.issues = [r for r in issues if keep(r)]
        self.products = self.W._products()
        self.whs = {w.x_bas_code: w for w in env['stock.warehouse'].search(
            [('x_bas_code', '!=', False), ('x_bas_kind', '=', 'warehouse')])}
        self.consumption = env.ref('KSW_inventory.location_consumption')
        self.t15, self.t0 = env['ksw.bas.gl.import']._purchase_taxes(self.company)
        self.vehicle_cc = self.W._vehicle_cost_centres({r['CC'] for r in self.issues if r['CC']})
        Analytic = env['account.analytic.account']
        self.store_cc = {wh: Analytic.search([('code', '=', code)], limit=1)
                         for wh, code in STORE_COST_CENTRE.items()}
        self.categs = self.products and env['product.category'].browse(
            {self.products[r['ICODE']].categ_id.id for r in self.purchases + self.issues})

    def stock_user(self, user_no):
        user_no = (user_no or '').strip() or '0'
        return self._user('bas.user.%s' % user_no,
                          self._norm(self._bas_user_names.get(user_no) or 'BAS user %s' % user_no),
                          list(STOCK_GROUPS))

    # ------------------------------------------------------------------ opening
    def opening(self):
        """Today's BAS quantity, backed out to 1 January; nothing posted."""
        Quant = self.env['stock.quant'].sudo()
        if Quant.search_count([('product_id', 'in', [p.id for p in self.products.values()]),
                               ('location_id.usage', '=', 'internal')]):
            return 'opening already loaded'
        flow = defaultdict(float)
        first_cost = {}
        for r in sorted(self.purchases + self.issues, key=lambda r: r['FDATE']):
            sign = 1 if r['FTYPE'] == '006' else -1
            flow[(r['ICODE'], r['CODE2'][1:])] += sign * r['QUAN']
            if r['QUAN'] and r['ICODE'] not in first_cost:
                first_cost[r['ICODE']] = r['AMOUNT'] / r['QUAN']
        now = self.W._bas_quantities_now({c for c, _w in flow})
        self.at(datetime(2025, 12, 31, 20, 0))
        self.categs.write({'property_valuation': 'periodic'})
        loaded, negative = [], []
        for (code, wh_code), moved in sorted(flow.items()):
            wh = self.whs.get(wh_code)
            qty = now.get((code, wh_code), 0.0) - moved
            if not wh:
                negative.append('%s@%s: no warehouse' % (code, wh_code))
                continue
            if qty < 0:
                negative.append('%s@W%s: BAS today %s, 2026 net %s' % (code, wh_code, now.get((code, wh_code), 0.0), moved))
                continue
            product = self.products[code].with_company(self.company)
            if not product.qty_available:
                product.sudo().standard_price = round(first_cost.get(code, product.standard_price), 4)
            if qty:
                Quant.with_context(inventory_mode=True).create({
                    'product_id': product.id, 'location_id': wh.lot_stock_id.id,
                    'inventory_quantity': qty,
                })._apply_inventory()
                loaded.append((code, wh.code, qty, product.standard_price))
        self.env['stock.move'].search([('is_inventory', '=', True), ('date', '>=', '2026-01-01')]).write(
            {'date': datetime(2025, 12, 31, 20, 0)})
        self.categs.write({'property_valuation': 'real_time'})
        self.commit()
        return {'loaded': loaded, 'refused': negative}

    # ------------------------------------------------------------------ replay
    def replay(self, date_from, date_to):
        docs = defaultdict(list)
        for r in self.purchases + self.issues:
            if date_from <= r['FDATE'].strftime('%Y-%m-%d') < date_to:
                docs['%s/%s/%s/%s' % (r['FTYPE'], r['FTYPE2'], r['CODE2'], r['N'])].append(r)
        done = set(self.env['stock.picking'].search([('x_bas_key', 'in', list(docs))]).mapped('x_bas_key'))
        for key, rows in sorted(docs.items(), key=lambda kv: (kv[1][0]['FDATE'], kv[0])):
            if key in done:
                continue
            fn = self._purchase if key.startswith('006/') else self._issue
            self.run_one('stock#' + key, fn, key, rows)
        return dict(self.log)

    def _drop_entry(self, key):
        entry = self.env['account.move'].search([('x_bas_key', '=', key)])
        if entry.line_ids.filtered('reconciled'):
            raise UserError('BAS entry %s is matched to a payment' % entry.name)
        entry.button_draft()
        entry.with_context(force_delete=True).unlink()

    def _voucher_rows(self, key):
        f, f2, c2, n = key.split('/')
        return self.bas("""SELECT RTRIM(ISNULL(FCODE,'')) FCODE, RTRIM(ISNULL(TCODE,'')) TCODE, AMOUNT,
            RTRIM(ISNULL(USER_NO,'')) USER_NO, RTRIM(ISNULL(COST_CENTER,'')) CC FROM vou10 WHERE FTYPE=%s AND FTYPE2=%s AND RTRIM(CODE2)=%s AND NUMBER1=%s""", (f, int(f2), c2, int(n)))

    def _purchase(self, key, rows):
        vou = self._voucher_rows(key)
        supplier = next((self.partner_by_code.get(v['TCODE']) for v in vou
                         if v['TCODE'].startswith('2101')), None)
        if not supplier:
            raise UserError('no supplier on %s' % key)
        vat = any(v['FCODE'] == '1209010001' and v['AMOUNT'] for v in vou)
        bas_total = round(sum(float(v['AMOUNT']) for v in vou if v['TCODE'].startswith('2101')), 2)
        wh = self.whs[rows[0]['CODE2'][1:]]
        when = rows[0]['FDATE'] - KSA
        user = self.stock_user(vou[0]['USER_NO'] if vou else None)
        self._drop_entry(key)
        self.at(when)
        PO = self.env['purchase.order'].with_user(user).sudo()
        po = PO.create({
            'partner_id': supplier.id, 'date_order': when, 'picking_type_id': wh.in_type_id.id,
            # BAS numbers a credit purchase with the supplier's invoice number
            # («ف6295» in its description): that is the vendor reference.
            'partner_ref': key.split('/')[-1],
            'order_line': [Command.create({
                'product_id': self.products[r['ICODE']].id, 'product_qty': r['QUAN'],
                'price_unit': r['AMOUNT'] / r['QUAN'] if r['QUAN'] else r['PRICE'],
                'tax_ids': [Command.set((self.t15 if vat else self.t0).ids)],
            }) for r in rows],
        })
        po.button_confirm()
        receipt = po.picking_ids
        receipt.write({'x_bas_key': key})
        for move in receipt.move_ids:
            move.write({'quantity': move.product_uom_qty, 'picked': True})
        receipt.with_context(skip_backorder=True, skip_sms=True).button_validate()
        po.action_create_invoice()
        bill = po.invoice_ids
        bill.write({'invoice_date': rows[0]['FDATE'].date(), 'ref': key.split('/')[-1], 'x_bas_key': key})
        bill.action_post()
        if abs(bill.amount_total - bas_total) > 0.05:
            self._record_problem(key + '/diff', 'purchase total',
                                 'Odoo %.2f vs BAS %.2f' % (bill.amount_total, bas_total))
        self.log['purchase'] += 1

    def _issue(self, key, rows):
        wh = self.whs[rows[0]['CODE2'][1:]]
        ptype = self.env['stock.picking.type'].search(
            [('warehouse_id', '=', wh.id), ('x_ksw_issue', '=', True)], limit=1)
        charge = self.env['account.account'].search([('code', '=', rows[0]['FCODE'])], limit=1)
        when = rows[0]['FDATE'] - KSA
        vou = self._voucher_rows(key)
        user = self.stock_user(vou[0]['USER_NO'] if vou else None)
        self._drop_entry(key)
        self.at(when)
        # bin10 cuts the vehicle name short («تيدر رقم 9» for «تيدر رقم 94»): the
        # voucher's charged line has it whole.
        name = next((v['CC'] for v in vou if v['FCODE'] and v['CC']), '') or rows[0]['CC']
        if name and _inv_norm(name) not in self.vehicle_cc:
            self.vehicle_cc.update(self.W._vehicle_cost_centres({name}))
        cc = self.vehicle_cc.get(_inv_norm(name)) if name else None
        cc = cc or self.store_cc[wh.x_bas_code].id
        Picking = self.env['stock.picking'].with_user(user).sudo()
        picking = Picking.create({
            'picking_type_id': ptype.id, 'location_id': wh.lot_stock_id.id,
            'location_dest_id': self.consumption.id, 'x_bas_key': key,
            'origin': name or False,     # what it was issued for: the vehicle
            'x_issue_account_id': charge.id if charge else False,
            'move_ids': [Command.create({
                'product_id': self.products[r['ICODE']].id, 'product_uom_qty': r['QUAN'],
                'location_id': wh.lot_stock_id.id, 'location_dest_id': self.consumption.id,
                'analytic_distribution': {str(cc): 100},
            }) for r in rows],
        })
        picking.action_confirm()
        for move in picking.move_ids:
            move.write({'quantity': move.product_uom_qty, 'picked': True})
        picking.with_context(skip_backorder=True, skip_sms=True).button_validate()
        if picking.state != 'done':
            raise UserError('issue %s not validated' % picking.name)
        self.log['issue'] += 1

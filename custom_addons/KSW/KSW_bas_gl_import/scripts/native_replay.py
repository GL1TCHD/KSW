"""Replay BAS9 (Jan-Sep 2026) into a fresh database through the NATIVE flows.

Not an import. Every BAS document is re-done the way the people who did it in
BAS would do it in Odoo today, through the same methods the screens call:

* a credit delivery note (600, FCODE 1203...) = the branch desk dispatches it
  (`ksw.water.dispatch.action_dispatch`) and the driver confirms it from his
  phone (`ksw.water.capture._receive`, the /water/sync path);
* a cash note (600, FCODE 1201... = the branch till) = a Cash Sale dispatch:
  the desk takes the cash, then either the customer's own tanker collects it
  (Sell and Deliver Now) or our driver delivers and confirms it;
* a month-end invoice (002/3) = Billing invoices exactly the notes BAS linked
  to it (`LINKED_*` on the 600 header) with `_water_create_invoice`;
* a customer collection (1203 credit line inside an 018/015 voucher) = an
  inbound `account.payment` in the bank/till journal, matched to the oldest
  open invoices;
* everything else in the ledger = the BAS voucher as a journal entry, so the
  trial balance still ties to BAS.

Each document runs AS its BAS user (desk = USER_NO, driver = COST_CENTER2)
and AT its BAS time: freezegun moves the clock and `cr._now` is set, so
create_date, date_done, dispatched_on, the chatter... all read the BAS moment.

Master data a person would set up first (branch config, users, a tanker's
driver, a client's price) is changed through the ORM at the moment it became
necessary, by the role that owns it, so it is in the chatter too.

Usage (odoo shell on the target DB only, never odoo_dev):

    .venv/bin/python odoo-bin shell -c KSW_bas_native.conf --no-http
    >>> exec(open('custom_addons/KSW/KSW_bas_gl_import/scripts/native_replay.py').read())
    >>> R = Replay(env); R.setup(); R.notes('172', '2026-01-01', '2026-01-08')
"""
import logging

import psycopg2
import psycopg2.errors
import re
import time
import uuid as uuidlib
from collections import Counter, defaultdict
from datetime import datetime, timedelta

from freezegun import freeze_time

from odoo import Command, fields
from odoo.exceptions import UserError

_logger = logging.getLogger('ksw.native_replay')

KSA = timedelta(hours=3)            # BAS stores Riyadh local time, Odoo UTC
GUARD_DB = 'odoo_bas_native'
BRANCHES = ('120', '135', '140', '145', '152', '160', '170', '171', '172',
            '175', '180', '185', '190')
CUSTOMER_CAR = re.compile(r'customer[s]? car', re.I)
DRIVER_GROUP = 'KSW_water_delivery.group_water_driver'
SUPERVISOR_GROUP = 'KSW_water_delivery.group_water_dispatcher'
BILLING_GROUP = 'KSW_water_delivery.group_water_billing'
ACCOUNTANT_GROUP = 'account.group_account_user'


def bas_key(ftype, ftype2, code2, number1):
    return f'{ftype}/{ftype2}/{(code2 or "").strip()}/{float(number1):.0f}'


# BAS driver names whose HR record is under another (English) name, confirmed
# by hand. Unmatched names are skipped and listed, never guessed.
DRIVER_ALIASES = {
    'Shafiq Nisar 566': 9953,
    'ربيع محمد عبد العزيز رضوان551': 9957,
    'مصطفي فايد 783': 9898,
    'jeesan nafeas 964': 10069,
    'ANWAR UL HAQ AKHTAR HUSSAIN': 10017,
    'عصام عبد السميع قديم': 10155,
    'نصر فهمي 654': 9763,
}


# Identified by Mohammed (2026-11-01): replayed natively once their HR record
# is in place, never neutralised as BAS-only.
PENDING_DRIVERS = ('عبد الرحيم 990', 'رباح عزت طه 721', 'صلاح الدين سعيد الله 1331',
                   'محمد عبد الرحمن 848', 'AMJID YOUNAS1217')


class Replay:

    def __init__(self, env):
        assert env.cr.dbname == GUARD_DB, 'native replay runs on %s only' % GUARD_DB
        self.env = env
        self.company = env.company
        self.imp = env['ksw.bas.gl.import']
        self._freezer = freeze_time('2025-12-31 06:00:00')
        self._frozen = self._freezer.start()
        self.log = Counter()
        self.problems = defaultdict(list)
        self._caches()

    # ------------------------------------------------------------------
    # clock
    # ------------------------------------------------------------------
    def at(self, when_utc):
        self._frozen.move_to(when_utc)
        self.env.cr._now = when_utc

    def stop(self):
        self._freezer.stop()

    def commit(self):
        self.env.cr.commit()

    # ------------------------------------------------------------------
    # BAS
    # ------------------------------------------------------------------
    def bas(self, sql, args=()):
        conn = self.imp._bas_connect()
        try:
            cur = conn.cursor(as_dict=True)
            cur.execute(sql, args)
            return cur.fetchall()
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # lookups
    # ------------------------------------------------------------------
    def _caches(self):
        env = self.env
        P = env['res.partner'].with_context(active_test=False)
        self.partner_by_code = {p.x_bas_code: p for p in P.search([('x_bas_code', '!=', False)])}
        Prod = env['product.product'].with_context(active_test=False)
        self.product_by_code = {p.x_bas_code: p for p in Prod.search([('x_bas_code', '!=', False)])}
        self.branch_by_code = {}
        for t in env['stock.picking.type'].search([('x_is_water_delivery', '=', True)]):
            code = t.warehouse_id.x_branch_code
            if code and code not in self.branch_by_code:
                self.branch_by_code[code] = t
        V = env['ksw.fleet.vehicle'].with_context(active_test=False).sudo()
        self.vehicle_by_cc = {}
        own = [False, self.company.partner_id.id]
        for v in V.search([('client_id', 'in', own)] if 'client_id' in V._fields else []):
            for k in filter(None, [(v.x_bas_cost_centre or '').strip(), (v.name or '').strip()]):
                self.vehicle_by_cc.setdefault(k.upper(), v)
        E = env['hr.employee'].with_context(active_test=False).sudo()
        self.employee_by_cc, self.employee_by_no = {}, {}
        for e in E.search([]):
            for k in filter(None, [e.x_bas_driver_cost_center, e.x_bas_driver_cost_center_alt]):
                self.employee_by_cc[self._norm(k)] = e
            if e.x_employee_no:
                self.employee_by_no.setdefault(e.x_employee_no.strip().lstrip('0'), e)
        for alias, emp_id in DRIVER_ALIASES.items():
            emp = E.browse(emp_id).exists()
            if emp:
                self.employee_by_cc[self._norm(alias)] = emp
        self.users_by_login = {u.login: u for u in env['res.users'].with_context(
            active_test=False).search([('login', '=like', 'bas.%')])}

    @staticmethod
    def _norm(s):
        return re.sub(r'\s+', ' ', (s or '').replace('\xa0', ' ')).strip()

    def vehicle_for(self, cc):
        cc = self._norm(cc)
        if not cc:
            return None
        v = self.vehicle_by_cc.get(cc.upper())
        if not v:
            digits = re.sub(r'\D', '', cc)
            if digits:
                v = self.vehicle_by_cc.get(digits) or self.vehicle_by_cc.get('T' + digits.zfill(3))
        return v

    def employee_for(self, cc):
        cc = self._norm(cc)
        if not cc:
            return None
        e = self.employee_by_cc.get(cc)
        if not e:
            m = re.search(r'(\d{2,6})\s*$', cc)
            if m and not cc.startswith('ايسوزو'):
                e = self.employee_by_no.get(m.group(1).lstrip('0'))
        return e

    # ------------------------------------------------------------------
    # users -- the people who did it in BAS
    # ------------------------------------------------------------------
    def _user(self, login, name, groups, employee=None):
        user = self.users_by_login.get(login) or self.env['res.users'].with_context(
            active_test=False).search([('login', '=', login)], limit=1)
        group_ids = [self.env.ref(g).id for g in groups]
        if not user:
            user = self.env['res.users'].sudo().with_context(no_reset_password=True).create({
                'name': name, 'login': login, 'company_id': self.company.id, 'tz': 'Asia/Riyadh',
                'company_ids': [Command.set([self.company.id])],
                'group_ids': [Command.set(group_ids + [self.env.ref('base.group_user').id])],
            })
            self.users_by_login[login] = user
            self.log['users created'] += 1
        elif not all(gid in user.group_ids.ids for gid in group_ids):
            user.sudo().write({'group_ids': [Command.link(g) for g in group_ids]})
        if employee and employee.user_id != user:
            employee.sudo().user_id = user
        return user

    def desk_user(self, user_no):
        user_no = (user_no or '').strip() or '0'
        if user_no not in self._bas_user_names:
            self._bas_user_names[user_no] = 'BAS user %s' % user_no
        return self._user('bas.user.%s' % user_no, self._norm(self._bas_user_names[user_no]),
                          [SUPERVISOR_GROUP])

    def driver_user(self, employee):
        if not employee.active:
            # He drove then and has left since: on the payroll at the time
            # of the note. Re-archived by finish() from this list.
            employee.sudo().active = True
            self.env.cr.execute("""CREATE TABLE IF NOT EXISTS ksw_replay_reactivated
                                   (employee_id int PRIMARY KEY)""")
            self.env.cr.execute("""INSERT INTO ksw_replay_reactivated VALUES (%s)
                                   ON CONFLICT DO NOTHING""", (employee.id,))
        if employee.user_id:
            if not employee.user_id.has_group(DRIVER_GROUP):
                employee.user_id.sudo().write({'group_ids': [Command.link(self.env.ref(DRIVER_GROUP).id)]})
            return employee.user_id
        return self._user('bas.driver.%s' % employee.id, employee.name, [DRIVER_GROUP], employee)

    _bas_user_names = {}

    def load_bas_users(self):
        type(self)._bas_user_names = {
            (r['USER_NO'] or '').strip(): (r['NAME'] or '').strip()
            for r in self.bas('SELECT USER_NO, NAME FROM USERS')}

    # ------------------------------------------------------------------
    # one-time setup: what an administrator configures before go-live
    # ------------------------------------------------------------------
    def setup(self):
        self.at(datetime(2025, 12, 31, 6, 0))
        self.load_bas_users()
        env = self.env
        # Historical notes carry no GPS: record, don't require, a position.
        tills = {r['b']: r['f'] for r in self.bas("""
            SELECT RTRIM(CODE2) b, RTRIM(FCODE) f, COUNT(*) n FROM vou10
            WHERE FTYPE='600' AND FDATE>='2026-01-01' AND FDATE<'2026-10-01'
              AND RTRIM(ISNULL(FCODE,'')) LIKE '1201%%'
            GROUP BY RTRIM(CODE2), RTRIM(FCODE)""")}
        sales = {}
        for r in self.bas("""
            SELECT RTRIM(CODE2) b, RTRIM(TCODE) t, COUNT(*) n FROM vou10
            WHERE FTYPE='600' AND FDATE>='2026-01-01' AND FDATE<'2026-10-01'
              AND RTRIM(ISNULL(TCODE,'')) LIKE '4%%'
            GROUP BY RTRIM(CODE2), RTRIM(TCODE)"""):
            if r['n'] > sales.get(r['b'], ('', 0))[1]:
                sales[r['b']] = (r['t'], r['n'])
        Account = env['account.account']
        Journal = env['account.journal']
        branch_plan = env['account.analytic.plan'].search([('name', '=', 'Branch Cost Centre')], limit=1)
        cost_centres = env['account.analytic.account'].search([('plan_id', 'child_of', branch_plan.id)])
        for code in BRANCHES:
            ptype = self.branch_by_code.get(code)
            if not ptype:
                self.problems['setup'].append('branch %s has no water operation type' % code)
                continue
            wh = ptype.warehouse_id
            vals = {'x_issue_mode': 'dispatch', 'x_location_rule': 'capture'}
            ptype.write(vals)
            wvals = {}
            if not wh.x_sales_account_id and code in sales:
                acc = Account.search([('code', '=', sales[code][0])], limit=1)
                wvals['x_sales_account_id'] = acc.id
            if not wh.x_cash_journal_id and code in tills:
                till = Account.search([('code', '=', tills[code])], limit=1)
                journal = Journal.search([('type', '=', 'cash'), ('default_account_id', '=', till.id)], limit=1)
                if journal:
                    wvals['x_cash_journal_id'] = journal.id
                    for line in journal.inbound_payment_method_line_ids.filtered(
                            lambda l: not l.payment_account_id):
                        line.payment_account_id = till
                else:
                    self.problems['setup'].append('branch %s till %s has no cash journal' % (code, tills[code]))
            if not wh.x_analytic_account_id:
                name = env['ksw.bas.branch'].search([('code', '=', code)], limit=1).with_context(lang='ar_001').name
                cc = cost_centres.filtered(lambda a: name and name in (a.name or ''))[:1]
                if cc:
                    wvals['x_analytic_account_id'] = cc.id
            if wvals:
                wh.write(wvals)
            if code in tills and not wh.x_cash_partner_id:
                ptype.action_create_cash_customer()
                self._own_receivable(wh.x_cash_partner_id)
        self.commit()
        self._caches()
        return self.problems['setup']

    def _own_receivable(self, partner):
        """Each BAS customer is its own 1203 account; the cash customers the
        admin creates get one too (as on the pilot: 120301492)."""
        Account = self.env['account.account']
        codes = sorted(int(a.code) for a in Account.search([('code', '=like', '120301___')]))
        account = Account.create({
            'code': str((codes[-1] if codes else 120301000) + 1),
            'name': partner.name, 'account_type': 'asset_receivable', 'reconcile': True,
        })
        partner.with_company(self.company).property_account_receivable_id = account

    # ------------------------------------------------------------------
    # delivery notes
    # ------------------------------------------------------------------
    def fetch_notes(self, branch, date_from, date_to):
        heads = self.bas("""
            SELECT h.FTYPE2, RTRIM(h.CODE2) CODE2, h.NUMBER1, RTRIM(h.FCODE) FCODE,
                   h.FDATE, RTRIM(ISNULL(h.USER_NO,'')) USER_NO, RTRIM(ISNULL(h.NAME,'')) NAME,
                   RTRIM(ISNULL(h.LINKED_FTYPE,'')) LF, h.LINKED_FTYPE2 LF2,
                   RTRIM(ISNULL(h.LINKED_CODE2,'')) LC2, h.LINKED_NUMBER1 LN1
            FROM vou10 h
            WHERE h.FTYPE='600' AND RTRIM(h.CODE2)=%s AND h.FDATE>=%s AND h.FDATE<%s
              AND RTRIM(ISNULL(h.FCODE,''))<>''
            ORDER BY h.FDATE, h.NUMBER1""", (branch, date_from, date_to))
        rev = {(r['FTYPE2'], r['NUMBER1']): r for r in self.bas("""
            SELECT FTYPE2, NUMBER1, RTRIM(ISNULL(COST_CENTER,'')) VEH, RTRIM(ISNULL(COST_CENTER2,'')) DRV,
                   ROW_NUMBER() OVER (PARTITION BY FTYPE2, NUMBER1 ORDER BY SERIAL) rn
            FROM vou10 WHERE FTYPE='600' AND RTRIM(CODE2)=%s AND FDATE>=%s AND FDATE<%s
              AND RTRIM(ISNULL(TCODE,'')) LIKE '4%%'""", (branch, date_from, date_to)) if r['rn'] == 1}
        lines = defaultdict(list)
        for r in self.bas("""
            SELECT FTYPE2, NUMBER1, SERIAL, RTRIM(ICODE) ICODE, QUAN, PRICE, AMOUNT
            FROM STR10 WHERE FTYPE='600' AND RTRIM(CODE2)=%s AND FDATE>=%s AND FDATE<%s
            ORDER BY SERIAL""", (branch, date_from, date_to)):
            lines[(r['FTYPE2'], r['NUMBER1'])].append(r)
        seen, out = set(), []
        for h in heads:
            k = (h['FTYPE2'], h['NUMBER1'])
            if k in seen:
                continue
            seen.add(k)
            h['key'] = bas_key('600', h['FTYPE2'], h['CODE2'], h['NUMBER1'])
            r = rev.get(k) or {}
            h['VEH'], h['DRV'] = r.get('VEH', ''), r.get('DRV', '')
            h['lines'] = lines.get(k, [])
            if h['LF'] and str(h['LN1'] or '').strip():
                h['invoice_key'] = bas_key(h['LF'], h['LF2'], h['LC2'], h['LN1'])
            out.append(h)
        return out

    def notes(self, branch, date_from, date_to, limit=None):
        t0 = time.time()
        done = set(self.env['stock.picking'].search([
            ('x_bas_key', '!=', False), ('x_bas_branch', '=', branch)]).mapped('x_bas_key'))
        heads = [h for h in self.fetch_notes(branch, date_from, date_to)
                 if not any(k in done for k in self._line_keys(h))]
        for h in (heads[:limit] if limit else heads):
            if self.run_one(h['key'], self.note, h):
                self.env.cr.execute("DELETE FROM ksw_replay_problem WHERE key = %s", (h['key'],))
                self.commit()
        self.commit()
        _logger.info('replay %s %s..%s: %s notes in %.0fs, %s', branch, date_from, date_to,
                     len(heads), time.time() - t0, dict(self.log))
        return dict(self.log)

    def run_one(self, key, fn, *args):
        """One BAS document = one transaction. Workers run branches side by
        side; a lock conflict rolls the whole transaction back and retries
        (REPEATABLE READ: a savepoint would keep the stale snapshot)."""
        for attempt in range(5):
            try:
                fn(*args)
                self.commit()
                return True
            except (psycopg2.errors.SerializationFailure, psycopg2.errors.DeadlockDetected,
                    psycopg2.errors.UniqueViolation, psycopg2.errors.LockNotAvailable) as e:
                self.env.cr.rollback()
                self.env.invalidate_all()
                self.users_by_login = {}
                time.sleep(0.2 * (attempt + 1))
                last = e
            except Exception as e:  # noqa: BLE001 - one document, not the run
                if any(t in str(e) for t in ('deadlock detected', 'could not serialize')) and attempt < 4:
                    self.env.cr.rollback()
                    self.env.invalidate_all()
                    self.users_by_login = {}
                    time.sleep(0.2 * (attempt + 1))
                    last = e
                    continue
                self.env.cr.rollback()
                self.env.invalidate_all()
                self.users_by_login = {}
                self.log['failed'] += 1
                self.problems[fn.__name__].append('%s: %s' % (key, str(e)[:300]))
                self._record_problem(key, fn.__name__, str(e))
                return False
        self.log['failed'] += 1
        self._record_problem(key, fn.__name__, 'gave up after retries: %s' % last)
        return False

    def _record_problem(self, key, step, message):
        self.env.cr.execute("""
            CREATE TABLE IF NOT EXISTS ksw_replay_problem (
                key varchar PRIMARY KEY, step varchar, message text, at timestamp DEFAULT now())""")
        self.env.cr.execute("""
            INSERT INTO ksw_replay_problem (key, step, message) VALUES (%s, %s, %s)
            ON CONFLICT (key) DO UPDATE SET step = EXCLUDED.step, message = EXCLUDED.message,
                                            at = now()""", (key, step, message[:2000]))
        self.env.cr.commit()

    @staticmethod
    def _line_keys(h):
        return ['%s#%s' % (h['key'], l['SERIAL']) for l in h['lines']] or [h['key']]

    def note(self, h):
        branch = self.branch_by_code[h['CODE2']]
        when = h['FDATE'] - KSA
        cash = h['FCODE'].startswith('1201')
        pickup = bool(CUSTOMER_CAR.search(h['VEH']) or CUSTOMER_CAR.search(h['DRV']))
        if cash:
            partner = branch.warehouse_id.x_cash_partner_id
        else:
            partner = self.partner_by_code.get(h['FCODE'])
        if not partner:
            raise UserError('no partner for %s' % h['FCODE'])
        desk = self.desk_user(h['USER_NO'])
        self.at(when)
        self._ensure_dispatcher(branch, desk)
        if not cash:
            self._ensure_coverage(branch, partner, desk)
        employee = vehicle = None
        if not pickup:
            employee = self.employee_for(h['DRV'])
            vehicle = self.vehicle_for(h['VEH'])
            if not vehicle and not h['VEH'] and employee:
                # BAS left the tanker blank (outside drivers, branch 160): the
                # one Fleet has him on is the one he drove.
                vehicle = self.env['ksw.fleet.vehicle'].sudo().search(
                    [('driver_id', '=', employee.id)], limit=1) or None
        missing = []
        if not pickup and not employee:
            missing.append('driver «%s» not found in Odoo HR' % (self._norm(h['DRV']) or 'blank in BAS'))
        if not pickup and not vehicle:
            missing.append('tanker «%s» not found in Odoo Fleet' % (self._norm(h['VEH']) or 'blank in BAS'))
        for line in h['lines']:
            product = self.product_by_code.get(line['ICODE'])
            qty = abs(float(line['QUAN'] or 0))
            if not product or not qty:
                raise UserError('line %s: product %s qty %s' % (line['SERIAL'], line['ICODE'], qty))
            price = round(float(line['PRICE'] or 0), 6)
            self._ensure_price(partner, product, qty, price, when)
            line_key = '%s#%s' % (h['key'], line['SERIAL'])
            if pickup:
                picking = self._pickup(branch, desk, partner, product, qty, h, cash)
            elif missing:
                picking = self._unlinked(branch, desk, partner, product, qty, h, cash, missing)
            else:
                picking = self._dispatch_and_confirm(branch, desk, partner, product, qty,
                                                     employee, vehicle, cash, h, when)
            picking.sudo().write({'x_bas_key': line_key, 'x_bas_branch': h['CODE2']})
            self.log[('cash ' if cash else 'credit ') + ('pickup' if pickup else 'unlinked' if missing else 'delivery')] += 1

    # --- the desk ------------------------------------------------------
    def _ensure_dispatcher(self, branch, desk):
        if desk not in branch.x_dispatcher_user_ids:
            branch.sudo().write({'x_dispatcher_user_ids': [Command.link(desk.id)]})

    def _ensure_coverage(self, branch, partner, desk):
        Coverage = self.env['ksw.water.client.branch'].sudo()
        code = branch.warehouse_id.x_branch_code
        if Coverage._partner_ids_for_branch(code) is None:
            return
        row = Coverage.with_context(active_test=False).search(
            [('partner_id', '=', partner.id), ('branch_code', '=', code)], limit=1)
        if not row:
            Coverage.with_user(desk).create({'partner_id': partner.id, 'branch_code': code})
        elif not row.active:
            row.active = True

    def _ensure_price(self, partner, product, qty, price, when):
        """The client's rule must say what BAS charged. When it does not, the
        rate changed that day: end the old rule a second before and start a
        dated one -- Client Rates, by Billing."""
        Pricing = self.env['ksw.water.pricing']
        billing = self.billing_user()
        rule = Pricing.with_user(billing)._rate_for(partner, product, qty)
        if rule and abs(rule.fixed_price - price) < 1e-6:
            return rule
        Item = self.env['product.pricelist.item'].with_user(billing).sudo()
        if rule and partner in self._cash_partners():
            # The desk's cash price list: Billing changes the rate, it does
            # not keep a dated history of every counter price.
            rule.with_user(billing).sudo().write({'fixed_price': price})
            self.log['rate set'] += 1
            return rule
        if rule and rule.date_start and rule.date_start >= when - timedelta(seconds=1):
            # Two notes in the same second at two prices: one rate, corrected.
            rule.with_user(billing).sudo().write({'fixed_price': price})
            self.log['rate set'] += 1
            return rule
        if rule:
            rule.with_user(billing).sudo().write({'date_end': when - timedelta(seconds=1)})
        self.log['rate set'] += 1
        return Item.create({
            'x_client_id': partner.commercial_partner_id.id,
            'product_id': product.id, 'compute_price': 'fixed', 'fixed_price': price,
            'min_quantity': rule.min_quantity if rule else 0.0,
            'date_start': when if rule else False,
        })

    def billing_user(self):
        if not hasattr(self, '_billing'):
            self._billing = self._user('bas.billing', 'Billing (BAS 002)', [BILLING_GROUP])
        return self._billing

    # --- the fleet -----------------------------------------------------
    def _ensure_tanker(self, branch, desk, employee, vehicle):
        """The supervisor leaves the driver on exactly this tanker, and the
        tanker on this branch (Fleet Management) -- only when it changed."""
        V = self.env['ksw.fleet.vehicle'].with_user(desk).sudo()
        if not vehicle.active:
            # Retired since; in service on the day of the note.
            vehicle.sudo().active = True
            self.env.cr.execute("""CREATE TABLE IF NOT EXISTS ksw_replay_reactivated_vehicle
                                   (vehicle_id int PRIMARY KEY)""")
            self.env.cr.execute("""INSERT INTO ksw_replay_reactivated_vehicle VALUES (%s)
                                   ON CONFLICT DO NOTHING""", (vehicle.id,))
        others = V.search([('driver_id', '=', employee.id), ('id', '!=', vehicle.id),
                           ('state', '=', 'confirmed')])
        if others:
            others.write({'driver_id': False})
        vals = {}
        if vehicle.driver_id != employee:
            vals['driver_id'] = employee.id
        if vehicle.state != 'confirmed':
            vals['state'] = 'confirmed'
        if vehicle.x_picking_type_id != branch:
            vals['x_picking_type_id'] = branch.id
        if vals:
            vehicle.with_user(desk).sudo().write(vals)
            self.log['tanker reassigned'] += 1

    # --- the three paths ----------------------------------------------
    def _dispatch_and_confirm(self, branch, desk, partner, product, qty, employee, vehicle,
                              cash, h, when):
        driver = self.driver_user(employee)
        self._ensure_tanker(branch, desk, employee, vehicle)
        Dispatch = self.env['ksw.water.dispatch'].with_user(desk)
        vals = {
            'picking_type_id': branch.id, 'partner_id': partner.id, 'product_id': product.id,
            'entered_qty': qty, 'entered_uom': 'product', 'driver_user_id': driver.id,
        }
        if cash:
            vals.update({'x_is_cash': True, 'x_collected_by': 'ours'})
        dispatch = Dispatch.create(vals)
        dispatch.action_dispatch()
        confirm = when + timedelta(seconds=1)
        self.at(confirm)
        stamp = fields.Datetime.to_string(confirm)
        capture = self.env['ksw.water.capture'].with_user(driver).sudo()._receive({
            'uuid': 'bas-%s' % uuidlib.uuid5(uuidlib.NAMESPACE_URL, h['key'] + str(product.id)),
            'dispatch_id': dispatch.id, 'vehicle_id': vehicle.id,
            'partner_id': partner.id, 'product_id': product.id,
            'entered_qty': qty, 'entered_uom': 'product',
            'captured_at': stamp, 'sent_at': stamp, 'sent_live': True,
            'app_version': 'bas9-replay',
        }, employee)
        if capture.state != 'issued':
            raise UserError('capture %s: %s' % (capture.state,
                                                capture.failure_reason or capture.hold_reason))
        return capture.picking_id

    def _unlinked(self, branch, desk, partner, product, qty, h, cash, missing):
        """Delivered by one of ours whom Odoo cannot name: the desk issues the
        note itself (Deliver Now), driver and tanker left empty, and the note
        says what BAS recorded instead."""
        text = 'BAS9: ' + '; '.join(missing) + '.'
        vals = {'picking_type_id': branch.id, 'partner_id': partner.id, 'product_id': product.id,
                'entered_qty': qty, 'entered_uom': 'product', 'x_collected_by': 'ours', 'note': text[:250]}
        if cash:
            vals.update({'x_is_cash': True, 'x_buyer_name': h['NAME'] or False, 'x_collected_by': 'ours'})
        dispatch = self.env['ksw.water.dispatch'].with_user(desk).create(vals)
        dispatch._water_sell_pickup()
        picking = dispatch.sudo().picking_id
        picking.sudo().write({'note': text})
        picking.sudo().message_post(body=text)
        dispatch.sudo().message_post(body=text)
        return picking

    def issue_skipped(self, date_from, date_to, branches=BRANCHES):
        """Every note of the period that was skipped: issued now, unlinked."""
        cr = self.env.cr
        cr.execute("""SELECT p.key FROM ksw_replay_problem p WHERE p.step = 'note' AND NOT EXISTS
                      (SELECT 1 FROM stock_picking sp WHERE sp.x_bas_key LIKE p.key || '#%%')""")
        keys = {k for (k,) in cr.fetchall()}
        for b in branches:
            mine = {k for k in keys if k.split('/')[2] == b}
            if not mine:
                continue
            for h in self.fetch_notes(b, date_from, date_to):
                if h['key'] in mine and self.run_one(h['key'], self.note, h):
                    cr.execute("DELETE FROM ksw_replay_problem WHERE key = %s", (h['key'],))
                    self.commit()

    def _pickup(self, branch, desk, partner, product, qty, h, cash=True):
        vals = {
            'picking_type_id': branch.id, 'partner_id': partner.id, 'product_id': product.id,
            'entered_qty': qty, 'entered_uom': 'product', 'x_collected_by': 'customer',
        }
        if cash:
            vals.update({'x_is_cash': True, 'x_buyer_name': h['NAME'] or False})
        dispatch = self.env['ksw.water.dispatch'].with_user(desk).create(vals)
        dispatch.action_dispatch()
        return dispatch.sudo().picking_id

    # ------------------------------------------------------------------
    # start over: wipe what a previous run made, keep the configuration
    # ------------------------------------------------------------------
    def reset_transactions(self):
        """Back to an empty ledger (clone only). Prices, users, tankers and
        branch settings made by setup stay; the pricelist rules the replay
        dated are removed so they are re-made at their own moments."""
        cr = self.env.cr
        cr.execute("""
            DELETE FROM ksw_water_invoice_wizard_picking_rel;
            UPDATE ksw_water_dispatch SET capture_id = NULL, picking_id = NULL;
            DELETE FROM ksw_water_capture; DELETE FROM ksw_water_dispatch;
            UPDATE res_company SET account_opening_move_id = NULL;
            DELETE FROM account_partial_reconcile; DELETE FROM account_full_reconcile;
            DELETE FROM account_payment; DELETE FROM account_analytic_line;
            DELETE FROM account_move_line; DELETE FROM account_move;
            DELETE FROM stock_move_line; DELETE FROM product_value; DELETE FROM stock_move;
            DELETE FROM stock_picking; DELETE FROM stock_reference;
            DELETE FROM sale_order_line; DELETE FROM sale_order;
            DELETE FROM product_pricelist_item WHERE date_start >= '2026-01-01'
                OR create_date >= '2025-12-31 00:00';
            UPDATE product_pricelist_item SET date_end = NULL WHERE date_end >= '2025-12-31';
            DROP TABLE IF EXISTS ksw_replay_problem;
        """)
        # Fresh numbering: what a new database starts at.
        seqs = self.env['stock.picking.type'].search([('x_is_water_delivery', '=', True)]).sequence_id
        seqs |= self.env['ir.sequence'].search([('code', 'in', ('sale.order', 'ksw.water.dispatch'))])
        for seq in seqs:
            seq.sudo().write({'number_next_actual': 1})
            seq.date_range_ids.sudo().write({'number_next_actual': 1})
        self.commit()

    # ------------------------------------------------------------------
    # more setup: the branch and the trucks BAS knows and Odoo did not
    # ------------------------------------------------------------------
    def setup_missing_branches(self):
        self.at(datetime(2025, 12, 31, 6, 0))
        env = self.env
        for code in BRANCHES:
            if code in self.branch_by_code:
                continue
            name = env['ksw.bas.branch'].search([('code', '=', code)], limit=1).name or code
            wh = env['stock.warehouse'].search([('x_branch_code', '=', code)], limit=1) \
                or env['stock.warehouse'].search([('code', '=', 'W%s' % code[1:])], limit=1)
            if wh and not wh.x_branch_code:
                wh.x_branch_code = code
            if not wh:
                wh = env['stock.warehouse'].create({'name': name, 'code': 'W%s' % code[1:],
                                                    'x_branch_code': code})
            template = self.branch_by_code['120']
            ptype = template.copy({
                'name': name, 'warehouse_id': wh.id, 'sequence_code': 'W%s' % code,
                'sequence_id': False,
                'default_location_src_id': wh.lot_stock_id.id,
                'x_dispatcher_user_ids': [Command.clear()],
            })
            self.branch_by_code[code] = ptype
        self.commit()

    def setup_missing_vehicles(self):
        """Every tanker BAS issued notes on is a company truck (WREF10). The
        ones the fleet does not have yet are registered, as Fleet would."""
        self.at(datetime(2025, 12, 31, 6, 0))
        rows = self.bas("""
            SELECT RTRIM(ISNULL(COST_CENTER,'')) veh, COUNT(*) n FROM vou10
            WHERE FTYPE='600' AND FDATE>='2026-01-01' AND FDATE<'2026-10-01'
              AND RTRIM(ISNULL(TCODE,'')) LIKE '4%%' GROUP BY RTRIM(ISNULL(COST_CENTER,''))""")
        V = self.env['ksw.fleet.vehicle'].sudo()
        made = []
        for r in rows:
            cc = self._norm(r['veh'])
            if not cc or CUSTOMER_CAR.search(cc) or self.vehicle_for(cc):
                continue
            digits = re.sub(r'\D', '', cc)
            if cc.startswith('ايسوزو') and digits:
                vals = {'name': digits, 'vehicle_type': 'isuzu', 'x_capacity_m3': 6.0}
            elif re.fullmatch(r'T\d+', cc):
                vals = {'name': 'T' + digits.zfill(3), 'vehicle_type': 'trailer', 'x_capacity_m3': 32.0}
            else:
                continue
            vals.update({'x_bas_cost_centre': cc, 'state': 'confirmed',
                         'client_id': self.company.partner_id.id})
            v = V.create(vals)
            self.vehicle_by_cc[cc.upper()] = v
            self.vehicle_by_cc.setdefault(v.name.upper(), v)
            made.append(cc)
        self.commit()
        return made

    # ------------------------------------------------------------------
    # opening balances: BAS's brought-forward, partner on every receivable
    # ------------------------------------------------------------------
    def opening(self):
        self.at(datetime(2025, 12, 31, 18, 0))
        res = self.imp.action_import_opening_balances(date='2025-12-31')
        move = self.env['account.move'].search([('x_bas_key', '=', 'OPENING/2026')])
        by_account = {}
        for p in self.env['res.partner'].with_context(active_test=False).search(
                [('x_bas_code', '=like', '1203%')]):
            by_account.setdefault(p.with_company(self.company).property_account_receivable_id.id, p)
        for line in move.line_ids.filtered(lambda l: l.account_id.account_type == 'asset_receivable'):
            partner = by_account.get(line.account_id.id)
            if partner:
                line.with_context(skip_account_move_synchronization=True).write(
                    {'partner_id': partner.id})
        self.commit()
        return res

    # ------------------------------------------------------------------
    # month end: Billing invoices what BAS invoiced
    # ------------------------------------------------------------------
    def month_end_invoices(self, date_from, date_to):
        heads = self.bas("""
            SELECT FTYPE2, RTRIM(CODE2) CODE2, NUMBER1, RTRIM(FCODE) FCODE, FDATE,
                   RTRIM(ISNULL(USER_NO,'')) USER_NO, AMOUNT, TAX_AMOUNT
            FROM vou10 WHERE FTYPE='002' AND FTYPE2=3 AND FDATE>=%s AND FDATE<%s
              AND RTRIM(ISNULL(FCODE,'')) LIKE '1203%%' ORDER BY FDATE, NUMBER1""",
            (date_from, date_to))
        done = {k.split('#')[0] for k in self.env['account.move'].search([('x_bas_key', 'like', '002/3/%')]).mapped('x_bas_key')}
        seen, jobs = set(), []
        for h in heads:
            key = bas_key('002', 3, h['CODE2'], h['NUMBER1'])
            if key in done or key in seen:
                continue
            seen.add(key)
            jobs.append((h['FDATE'], key, self._invoice_002, (key, h)))
        return jobs

    def _invoice_002(self, key, h):
        """BAS splits a client's month into one 002 per revenue line (trailers,
        ex-factory...), and the LINKED_* stamp on the notes is overwritten by
        whichever was saved last -- so it is not the grouping. The grouping is
        the revenue account: each 002 bills the client's open notes that post
        to its own revenue lines, up to its amount. A 002 with no notes behind
        it (camps, services) is invoiced from its own lines."""
        rows = self.bas("""
            SELECT SERIAL, RTRIM(ISNULL(FCODE,'')) FCODE, RTRIM(ISNULL(TCODE,'')) TCODE, AMOUNT,
                   BAMOUNT, TAX_AMOUNT, FDATE, INVACCPOS, CODE2, FTYPE, FTYPE2, NUMBER1,
                   RTRIM(ISNULL(USER_NO,'')) USER_NO
            FROM vou10 WHERE FTYPE='002' AND FTYPE2=3 AND RTRIM(CODE2)=%s AND NUMBER1=%s
            ORDER BY SERIAL""", (h['CODE2'], h['NUMBER1']))
        wanted = defaultdict(float)
        for r in rows:
            if r['TCODE'].startswith('4') and float(r['AMOUNT'] or 0) > 0:
                wanted[r['TCODE']] += float(r['AMOUNT'])
        partner = self.partner_by_code.get(h['FCODE'])
        when = h['FDATE'] - KSA
        customers = [r for r in rows if r['FCODE'].startswith('1203') and float(r['AMOUNT'] or 0) > 0]
        if len({r['FCODE'] for r in customers}) > 1:
            # One BAS 002 for several customer accounts (International Energy's
            # sites). In Odoo each site is its own client, so one invoice each.
            return self._invoice_002_per_site(key, h, customers, wanted, when)
        notes = self._notes_for(partner, wanted, when) if partner else self.env['stock.picking']
        self.at(when)
        billing = self.billing_user(h['USER_NO'])
        if not notes:
            return self._invoice_001(key, ('002', 3, h['CODE2'], h['NUMBER1']), rows)
        invoice = notes.with_user(billing).sudo()._water_create_invoice(invoice_date=h['FDATE'].date())
        invoice.sudo().write({'x_bas_key': key, 'x_bas_branch': h['CODE2'],
                              'ref': 'BAS 002/3 #%.0f' % h['NUMBER1']})
        invoice.with_user(billing).sudo().action_post()
        bas_total = round(sum(float(r['AMOUNT'] or 0) for r in rows if r['FCODE'].startswith('1203')), 2)
        # In-close the fill in _notes_for already took every open note it
        # could; a supplementary invoice is only for closed months (patch).
        total = invoice.amount_total
        if bas_total - total > 0.05:
            total += self._rest_as_bas(key, h, partner, bas_total - total, wanted, billing)
        if abs(total - bas_total) > 0.05:
            self._record_problem(key + '/diff', 'invoice total', 'Odoo %.2f vs BAS %.2f (%d notes)' % (
                total, bas_total, len(notes)))
        self.log['month-end invoice'] += 1

    # ------------------------------------------------------------------
    # notes that could not be issued natively: kept exactly as BAS has them
    # ------------------------------------------------------------------
    def _fallback_table(self):
        self.env.cr.execute("""
            CREATE TABLE IF NOT EXISTS ksw_replay_fallback (
                key varchar PRIMARY KEY, branch varchar, fdate timestamp, fcode varchar,
                kind varchar, account varchar, amount numeric, tax numeric, user_no varchar,
                value varchar, pending boolean, move_id int, done_at timestamp)""")

    def collect_skipped(self):
        """Every skipped note, with what BAS posted for it, once."""
        self._fallback_table()
        cr = self.env.cr
        cr.execute("""SELECT p.key, p.message FROM ksw_replay_problem p
                      LEFT JOIN ksw_replay_fallback f ON f.key = p.key
                      WHERE p.step = 'note' AND f.key IS NULL""")
        todo = defaultdict(dict)
        for key, msg in cr.fetchall():
            _, f2, b, n = key.split('/')
            todo[b][(int(f2), int(n))] = (key, msg)
        pending = {self._norm(x) for x in PENDING_DRIVERS}
        for b, items in todo.items():
            nums = sorted({n for _, n in items})
            for i in range(0, len(nums), 400):
                chunk = ','.join(str(x) for x in nums[i:i + 400])
                heads = {}
                for r in self.bas(f"""SELECT FTYPE2, NUMBER1, FDATE, RTRIM(FCODE) FCODE, RTRIM(ISNULL(USER_NO,'')) USER_NO
                        FROM vou10 WHERE FTYPE='600' AND RTRIM(CODE2)=%s AND NUMBER1 IN ({chunk})
                          AND RTRIM(ISNULL(FCODE,''))<>''""", (b,)):
                    heads.setdefault((r['FTYPE2'], int(r['NUMBER1'])), r)
                rev = defaultdict(list)
                for r in self.bas(f"""SELECT FTYPE2, NUMBER1, RTRIM(TCODE) TCODE, RTRIM(ISNULL(COST_CENTER,'')) VEH,
                        RTRIM(ISNULL(COST_CENTER2,'')) DRV FROM vou10 WHERE FTYPE='600' AND RTRIM(CODE2)=%s
                        AND NUMBER1 IN ({chunk}) AND RTRIM(ISNULL(TCODE,'')) LIKE '4%%' ORDER BY SERIAL""", (b,)):
                    rev[(r['FTYPE2'], int(r['NUMBER1']))].append(r)
                amt = defaultdict(lambda: [0.0, 0.0])
                for r in self.bas(f"""SELECT FTYPE2, NUMBER1, AMOUNT, TAX_AMOUNT FROM STR10
                        WHERE FTYPE='600' AND RTRIM(CODE2)=%s AND NUMBER1 IN ({chunk})""", (b,)):
                    a = amt[(r['FTYPE2'], int(r['NUMBER1']))]
                    a[0] += float(r['AMOUNT'] or 0); a[1] += float(r['TAX_AMOUNT'] or 0)
                for k, (key, msg) in items.items():
                    if k[1] not in {int(x) for x in chunk.split(',')}:
                        continue
                    h, rv = heads.get(k), rev.get(k, [{}])[0]
                    if not h:
                        continue
                    drv = self._norm(rv.get('DRV', ''))
                    cr.execute("""INSERT INTO ksw_replay_fallback
                        (key, branch, fdate, fcode, kind, account, amount, tax, user_no, value, pending)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""", (
                        key, b, h['FDATE'], h['FCODE'], 'cash' if h['FCODE'].startswith('1201') else 'credit',
                        rv.get('TCODE'), round(amt[k][0], 2), round(amt[k][1], 2), h['USER_NO'],
                        drv or self._norm(rv.get('VEH', '')), drv in pending))
        self.commit()

    def fallback_cash(self, date_from, date_to):
        """A cash note BAS issued and Odoo could not: BAS's own voucher, as is."""
        self._fallback_table()
        cr = self.env.cr
        cr.execute("""SELECT key, branch, fdate, user_no FROM ksw_replay_fallback
                      WHERE kind='cash' AND NOT pending AND move_id IS NULL
                        AND fdate >= %s AND fdate < %s ORDER BY fdate""", (date_from, date_to))
        todo = cr.fetchall()
        accounts = {a.x_bas_code: a.id for a in self.env['account.account'].search([('x_bas_code', '!=', False)])}
        for key, b, fdate, user_no in todo:
            self.run_one(key + '/bas', self._cash_as_bas, key, b, fdate, user_no, accounts)

    def _cash_as_bas(self, key, b, fdate, user_no, accounts):
        _, f2, _, n = key.split('/')
        lines = self.bas("""SELECT FTYPE, FTYPE2, RTRIM(CODE2) CODE2, NUMBER1, SERIAL, RTRIM(FCODE) FCODE,
                RTRIM(TCODE) TCODE, AMOUNT, BAMOUNT, TAX_AMOUNT, FDATE FROM vou10
                WHERE FTYPE='600' AND FTYPE2=%s AND RTRIM(CODE2)=%s AND NUMBER1=%s ORDER BY SERIAL""",
                         (int(f2), b, int(n)))
        vals = self.imp.with_context(bas_journal_cache={})._voucher_to_move(
            key, '600', int(f2), b, float(n), lines, accounts, accounts.get('2119010001'),
            self.company, self.problems['gl'])
        if vals is None:
            raise UserError('BAS voucher not postable: %s' % (self.problems['gl'][-1:] or ''))
        self.at(fdate - KSA)
        user = self.accountant(user_no)
        vals['ref'] = '%s (BAS note, not issued in Odoo)' % vals['ref']
        move = self.env['account.move'].with_user(user).sudo().with_context(
            check_move_validity=False).create(vals)
        move._post(soft=False)
        self.env.cr.execute('UPDATE ksw_replay_fallback SET move_id=%s, done_at=now() WHERE key=%s',
                            (move.id, key))
        self.log['cash note as BAS'] += 1

    def _residual_invoice(self, key, h, partner, wanted, invoice, billing):
        """The 002 billed notes Odoo could not issue. Billing invoices them on
        their own, same client, same revenue line, same date -- so what the
        client owes and what was earned is what BAS says."""
        self._fallback_table()
        if not partner:
            return None
        cr = self.env.cr
        invoiced = defaultdict(float)
        for line in invoice.invoice_line_ids.filtered(lambda l: l.display_type == 'product'):
            invoiced[line.account_id.code] += line.price_subtotal
        codes = [p.x_bas_code for p in self.env['res.partner'].with_context(active_test=False).search(
            [('commercial_partner_id', '=', partner.commercial_partner_id.id), ('x_bas_code', '!=', False)])]
        short = round(sum(wanted.values()) - sum(invoiced.values()), 2)
        short_by = {'_shortfall': short} if short > 0.05 else {}
        if short_by:
            notes = self._notes_for(partner, short_by, h['FDATE'] - KSA)
            if notes:
                extra = notes.with_user(billing).sudo()._water_create_invoice(invoice_date=h['FDATE'].date())
                extra.sudo().write({'x_bas_key': key + '#notes', 'x_bas_branch': h.get('CODE2') or False,
                                    'ref': 'BAS 002/3 #%.0f (notes issued later)' % h['NUMBER1']})
                extra.with_user(billing).sudo().action_post()
                self.log['supplementary invoice'] += 1
                return extra
        return None
        lines, keys = [], []
        for code, amount in []:
            short = 0
            cr.execute("""SELECT key, amount, fdate FROM ksw_replay_fallback
                          WHERE kind='credit' AND NOT pending AND move_id IS NULL AND account=%s
                            AND fcode = ANY(%s) AND fdate <= %s ORDER BY fdate""",
                       (code, codes, h['FDATE']))
            candidates = cr.fetchall()
            run = 0.0
            account = self.env['account.account'].search([('code', '=', code)], limit=1)
            for nkey, namount, fdate in candidates:
                if run + float(namount) > short + 0.05:
                    break
                run += float(namount)
                keys.append(nkey)
                lines.append(Command.create({
                    'name': 'BAS note %s · %s (not issued in Odoo)' % (
                        nkey.split('/', 2)[2], fdate.date()),
                    'account_id': account.id, 'quantity': 1, 'price_unit': float(namount),
                    'tax_ids': [Command.set(self._sale_tax15().ids)],
                }))
        if not lines:
            return None
        extra = self.env['account.move'].with_user(billing).sudo().create({
            'move_type': 'out_invoice', 'partner_id': invoice.partner_id.id,
            'invoice_date': h['FDATE'].date(), 'journal_id': invoice.journal_id.id,
            'ref': 'BAS 002/3 #%.0f: notes not issued in Odoo' % h['NUMBER1'],
            'invoice_line_ids': lines, 'x_bas_key': key + '#notes',
        })
        extra.action_post()
        cr.execute('UPDATE ksw_replay_fallback SET move_id=%s, done_at=now() WHERE key = ANY(%s)',
                   (extra.id, keys))
        self.log['credit notes as BAS'] += len(keys)
        return extra

    def _sale_tax15(self):
        if not hasattr(self, '_t15'):
            self._t15 = self.imp._sale_taxes(self.company)[0]
        return self._t15

    def patch_month_end(self, date_from, date_to):
        """For a month already closed: the residual invoices its 002s needed."""
        moves = self.env['account.move'].search([
            ('x_bas_key', '=like', '002/3/%'), ('x_bas_key', 'not like', '%#%'),
            ('date', '>=', date_from), ('date', '<', date_to)],
            order='date, id')
        for inv in moves:
            if self.env['account.move'].search_count([('x_bas_key', '=', inv.x_bas_key + '#notes')]):
                continue
            self.run_one(inv.x_bas_key + '#notes', self._patch_one_002, inv.id)

    def _patch_one_002(self, inv_id):
        inv = self.env['account.move'].browse(inv_id)
        _, _, c2, n = inv.x_bas_key.split('/')
        rows = self.bas("""SELECT RTRIM(ISNULL(FCODE,'')) FCODE, RTRIM(ISNULL(TCODE,'')) TCODE, AMOUNT, FDATE,
                RTRIM(ISNULL(USER_NO,'')) USER_NO FROM vou10 WHERE FTYPE='002' AND FTYPE2=3
                AND RTRIM(CODE2)=%s AND NUMBER1=%s""", (c2, int(n)))
        wanted = defaultdict(float)
        for r in rows:
            if r['TCODE'].startswith('4') and float(r['AMOUNT'] or 0) > 0:
                wanted[r['TCODE']] += float(r['AMOUNT'])
        head = next((r for r in rows if r['FCODE'].startswith('1203')), None)
        if not head or not inv.line_ids.sale_line_ids:
            return   # invoiced from its own lines: already BAS's amount
        h = {'FDATE': head['FDATE'], 'NUMBER1': float(n)}
        self.at(head['FDATE'] - KSA)
        self._residual_invoice(inv.x_bas_key, h, self.partner_by_code.get(head['FCODE']), wanted, inv,
                               self.billing_user(head['USER_NO']))

    def _rest_as_bas(self, key, h, partner, short_total, wanted, billing):
        """What BAS billed on this 002 beyond the notes Odoo has for it
        (deliveries before 1 Jan 2026, price or VAT rounding): one line, as BAS."""
        account = self.env['account.account'].search([('code', '=', max(wanted, key=wanted.get))], limit=1) \
            if wanted else partner.property_account_receivable_id
        untaxed = round(short_total / 1.15, 2)
        inv = self.env['account.move'].with_user(billing).sudo().create({
            'move_type': 'out_invoice', 'partner_id': partner.id, 'invoice_date': h['FDATE'].date(),
            'ref': 'BAS 002/3 #%.0f: amount not covered by notes in Odoo' % h['NUMBER1'],
            'x_bas_key': key + '#rest',
            'invoice_line_ids': [Command.create({
                'name': 'BAS 002/3 #%.0f: amount not covered by notes in Odoo '
                        '(earlier deliveries, price or VAT rounding)' % h['NUMBER1'],
                'account_id': account.id, 'quantity': 1, 'price_unit': untaxed,
                'tax_ids': [Command.set(self._sale_tax15().ids)]})],
        })
        inv.action_post()
        self.log['rest as BAS'] += 1
        return inv.amount_total

    def _invoice_002_per_site(self, key, h, customers, wanted, when):
        self.at(when)
        billing = self.billing_user(h['USER_NO'])
        Move = self.env['account.move'].with_user(billing).sudo()
        account = self.env['account.account'].search(
            [('code', '=', max(wanted, key=wanted.get))], limit=1) if wanted else None
        per_site = defaultdict(float)
        for r in customers:
            per_site[r['FCODE']] += float(r['AMOUNT'])
        customers = [{'FCODE': code, 'AMOUNT': amount} for code, amount in per_site.items()]
        for r in customers:
            site = self.partner_by_code.get(r['FCODE'])
            if not site:
                raise UserError('no partner for %s' % r['FCODE'])
            untaxed = round(float(r['AMOUNT']) / 1.15, 2)
            notes = self._notes_for(site, {'_site': untaxed}, when)
            if notes:
                inv = notes.with_user(billing).sudo()._water_create_invoice(invoice_date=h['FDATE'].date())
            else:
                inv = Move.create({
                    'move_type': 'out_invoice', 'partner_id': site.id, 'invoice_date': h['FDATE'].date(),
                    'invoice_line_ids': [Command.create({
                        'name': 'BAS 002/3 #%.0f' % h['NUMBER1'], 'account_id': account.id,
                        'quantity': 1, 'price_unit': untaxed,
                        'tax_ids': [Command.set(self._sale_tax15().ids)]})]})
            inv.sudo().write({'x_bas_key': '%s#%s' % (key, r['FCODE']), 'x_bas_branch': h['CODE2'],
                              'ref': 'BAS 002/3 #%.0f' % h['NUMBER1']})
            inv.with_user(billing).sudo().action_post()
            if float(r['AMOUNT']) - inv.amount_total > 0.05:
                self._rest_as_bas('%s#%s' % (key, r['FCODE']), h, site,
                                  float(r['AMOUNT']) - inv.amount_total, wanted, billing)
                continue
            if abs(inv.amount_total - float(r['AMOUNT'])) > 0.05:
                self._record_problem('%s#%s/diff' % (key, r['FCODE']), 'invoice total',
                                     'Odoo %.2f vs BAS %.2f (%d notes)' % (inv.amount_total, float(r['AMOUNT']), len(notes)))
        self.log['month-end invoice (per site)'] += len(customers)

    def _note_account(self, note):
        line = note.sale_id.order_line[:1]
        Rev = self.env['ksw.water.revenue.account']
        return (Rev._account_for(note.picking_type_id.warehouse_id.x_branch_code, line.product_id)
                or note.picking_type_id.warehouse_id.x_sales_account_id
                or line.product_id.product_tmpl_id._get_product_accounts()['income'])

    def _notes_for(self, partner, wanted, when):
        # The BAS customer itself (a site is its own 1203 account), never its
        # siblings under the same parent company: pooling by the parent let one
        # site's 002 take another site's notes, which were then billed twice.
        open_notes = self.env['stock.picking'].search([
            ('partner_id', 'child_of', partner.ids),
            ('x_is_water_delivery', '=', True), ('state', '=', 'done'),
            ('x_invoice_status', '=', 'to invoice'), ('date_done', '<=', when),
        ], order='date_done, id')
        by_account = defaultdict(list)
        for note in open_notes:
            by_account[self._note_account(note).code].append(note)
        chosen = self.env['stock.picking']
        for code, amount in wanted.items():
            pool = by_account.get(code, [])
            total = sum(n.x_amount_untaxed for n in pool)
            if abs(total - amount) <= 0.05 or total < amount:
                chosen |= self.env['stock.picking'].union(*pool) if pool else chosen.browse()
                continue
            run = 0.0
            for note in pool:            # oldest first, until the 002 is filled
                if run + note.x_amount_untaxed > amount + 0.05:
                    break
                chosen |= note
                run += note.x_amount_untaxed
        # Still short: the client's other open notes, whatever account Odoo's
        # rule gives them (BAS can post the same water to another revenue line,
        # e.g. Nabiya by Isuzu). The total follows BAS; the account, Odoo.
        left = sum(wanted.values()) - sum(chosen.mapped('x_amount_untaxed'))
        for note in open_notes - chosen:
            if left <= 0.05:
                break
            if note.x_amount_untaxed <= left + 0.05:
                chosen |= note
                left -= note.x_amount_untaxed
        return chosen

    def _invoice_002_by_link(self, key, h):
        linked = self.bas("""
            SELECT FTYPE2, RTRIM(CODE2) CODE2, NUMBER1 FROM vou10
            WHERE FTYPE='600' AND RTRIM(ISNULL(FCODE,'')) <> ''
              AND LINKED_FTYPE='002' AND LINKED_FTYPE2='3'
              AND RTRIM(LINKED_CODE2)=%s AND LINKED_NUMBER1=%s""",
            (h['CODE2'], '%.0f' % h['NUMBER1']))
        prefixes = {bas_key('600', r['FTYPE2'], r['CODE2'], r['NUMBER1']) for r in linked}
        Picking = self.env['stock.picking']
        notes = Picking.browse()
        for chunk in _chunks(sorted(prefixes), 500):
            notes |= Picking.search([('x_bas_key', 'in', [p + '#%d' % i for p in chunk for i in range(1, 6)])])
        notes = notes.filtered(lambda p: p.state == 'done' and p.x_invoice_status == 'to invoice')
        if not notes:
            raise UserError('no replayed notes for %s (%d linked in BAS)' % (key, len(prefixes)))
        when = h['FDATE'] - KSA
        self.at(when)
        billing = self.billing_user(h['USER_NO'])
        invoice = notes.with_user(billing).sudo()._water_create_invoice(invoice_date=h['FDATE'].date())
        invoice.sudo().write({'x_bas_key': key, 'x_bas_branch': h['CODE2'], 'ref': 'BAS 002/3 #%.0f' % h['NUMBER1']})
        invoice.with_user(billing).sudo().action_post()
        bas_total = round(float(h['AMOUNT'] or 0), 2)
        if abs(invoice.amount_total - bas_total) > 0.05:
            self._record_problem(key + '/diff', 'invoice total',
                                 'Odoo %.2f vs BAS %.2f (%d of %d notes)' % (
                                     invoice.amount_total, bas_total, len(notes), len(prefixes)))
        self.log['month-end invoice'] += 1

    def _cash_partners(self):
        if not hasattr(self, '_cash_partner_set'):
            self._cash_partner_set = self.env['stock.warehouse'].search([]).x_cash_partner_id
        return self._cash_partner_set

    def billing_user(self, user_no=None):
        if user_no:
            return self._user('bas.user.%s' % user_no.strip(),
                              self._norm(self._bas_user_names.get(user_no.strip()) or 'BAS user %s' % user_no),
                              [BILLING_GROUP])
        if not hasattr(self, '_billing'):
            self._billing = self._user('bas.billing', 'Billing (BAS 002)', [BILLING_GROUP])
        return self._billing

    # ------------------------------------------------------------------
    # collections: a customer line inside an 018/015 voucher
    # ------------------------------------------------------------------
    def vouchers(self, date_from, date_to):
        """Every BAS voucher of the period that is not a delivery note or a
        month-end invoice: customer collections become payments, standard
        invoices (001) invoices, the rest journal entries."""
        rows = self.bas("""
            SELECT FTYPE, FTYPE2, RTRIM(CODE2) CODE2, NUMBER1, SERIAL,
                   RTRIM(FCODE) FCODE, RTRIM(TCODE) TCODE, AMOUNT, BAMOUNT, TAX_AMOUNT, FDATE,
                   RTRIM(ISNULL(USER_NO,'')) USER_NO, RTRIM(ISNULL(REMARK,'')) REMARK
            FROM vou10 WHERE FDATE>=%s AND FDATE<%s
              AND FTYPE <> '600' AND NOT (FTYPE='002' AND FTYPE2=3)
            ORDER BY FDATE, FTYPE, FTYPE2, CODE2, NUMBER1, SERIAL""", (date_from, date_to))
        vouchers = defaultdict(list)
        for r in rows:
            vouchers[(r['FTYPE'], r['FTYPE2'], r['CODE2'], r['NUMBER1'])].append(r)
        done = {k.split('#')[0] for k in self.env['account.move'].search([
            ('x_bas_key', '!=', False), ('date', '>=', date_from), ('date', '<', date_to)]).mapped('x_bas_key')}
        accounts = {a.x_bas_code: a.id for a in self.env['account.account'].search([('x_bas_code', '!=', False)])}
        jobs = []
        for k, lines in vouchers.items():
            key = bas_key(*k)
            if key in done:
                continue
            if k[0] == '001':
                jobs.append((lines[0]['FDATE'], key, self._invoice_001, (key, k, lines)))
            else:
                jobs.append((lines[0]['FDATE'], key, self._voucher, (key, k, lines, accounts)))
        return jobs

    def _invoice_001(self, key, k, lines):
        if not hasattr(self, '_inv_ctx'):
            self._inv_ctx = (self.imp._sale_taxes(self.company),
                             {a.x_bas_code: a for a in self.env['account.account'].search([('x_bas_code', '!=', False)])})
        (t15, t0), accounts = self._inv_ctx
        vals, cash_acc = self.imp.with_context(bas_journal_cache={}, bas_cash_journals={})._build_invoice(
            key, k, lines, None, accounts, self.partner_by_code, self.product_by_code, t15, t0,
            self.imp._journal(k[0], self.company), self.company, self.problems[k[0]])
        if vals is None:
            # Not a sale to a customer (e.g. a fixed asset sold, counterpart
            # 1208...): BAS's voucher as a journal entry, as BAS posted it.
            accs = {a.x_bas_code: a.id for a in self.env['account.account'].search([('x_bas_code', '!=', False)])}
            return self._voucher(key, k, lines, accs)
        self.at(lines[0]['FDATE'] - KSA)
        billing = self.billing_user(lines[0]['USER_NO'])
        move = self.env['account.move'].with_user(billing).sudo().create(vals)
        move.action_post()
        if cash_acc:
            # Paid at the till: received there, the way BAS booked it.
            self.imp.with_user(billing).sudo().with_context(bas_cash_journals={})._settle_cash_invoices(
                move, [cash_acc], self.company)
            receipts = (move.line_ids.matched_credit_ids.credit_move_id.move_id
                        | move.line_ids.matched_debit_ids.debit_move_id.move_id) - move
            for n, receipt in enumerate(receipts):
                receipt.sudo().x_bas_key = '%s#cash%d' % (key, n)
        self.log['standard invoice' if k[0] == '001' else 'month-end invoice (no notes)'] += 1

    def _voucher(self, key, k, lines, accounts):
        ftype, ftype2, code2, number1 = k
        self.at(lines[0]['FDATE'] - KSA)
        rest = [dict(r) for r in lines]
        if ftype in ('015', '018'):
            rest = self._collections(key, rest)
        if not rest:
            return
        vals = self.imp._voucher_to_move(key, ftype, ftype2, code2, number1, rest, accounts,
                                         accounts.get('2119010001'), self.company, self.problems['gl'])
        if vals is None:
            raise UserError('voucher not postable: %s' % (self.problems['gl'][-1:] or ''))
        accountant = self.accountant(lines[0]['USER_NO'])
        move = self.env['account.move'].with_user(accountant).sudo().with_context(
            check_move_validity=False).create(vals)
        move._post(soft=False)
        self.log['journal entry'] += 1

    def accountant(self, user_no):
        user_no = (user_no or '').strip() or '0'
        return self._user('bas.user.%s' % user_no,
                          self._norm(self._bas_user_names.get(user_no) or 'BAS user %s' % user_no),
                          [ACCOUNTANT_GROUP])

    def _collections(self, key, rows):
        """Customer credit lines become inbound payments in the journal of
        the voucher's one bank/till debit; what is left stays the entry."""
        cust = [r for r in rows if (r['TCODE'] or '').startswith('1203') and float(r['AMOUNT'] or 0) > 0]
        if not cust:
            return rows
        debit_codes = {r['FCODE'] for r in rows if r['FCODE'] and r['FCODE'][:4] in ('1201', '1202')}
        if len(debit_codes) != 1:
            return rows
        code = debit_codes.pop()
        account = self.env['account.account'].search([('code', '=', code)], limit=1)
        journal = self.env['account.journal'].search([('type', 'in', ('bank', 'cash')),
                                                      ('default_account_id', '=', account.id)], limit=1)
        if not journal:
            return rows
        covered = sum(float(r['AMOUNT'] or 0) for r in rows if r['FCODE'] == code)
        payable = sum(float(r['AMOUNT'] or 0) for r in cust if self.partner_by_code.get(r['TCODE']))
        if payable > covered + 0.005:
            # Part of the money came in through another account: netting the
            # collections against this one debit would unbalance the voucher.
            return rows
        accountant = self.accountant(rows[0]['USER_NO'])
        Payment = self.env['account.payment'].with_user(accountant).sudo()
        total = 0.0
        for r in cust:
            partner = self.partner_by_code.get(r['TCODE'])
            if not partner:
                continue
            amount = round(float(r['AMOUNT']), 2)
            pay = Payment.create({
                'payment_type': 'inbound', 'partner_type': 'customer',
                'partner_id': partner.id, 'amount': amount, 'journal_id': journal.id,
                'date': r['FDATE'].date(), 'memo': (r['REMARK'] or key)[:200],
            })
            pay.action_post()
            pay.move_id.sudo().x_bas_key = '%s#%s' % (key, r['SERIAL'])
            self._match_oldest(pay)
            r['AMOUNT'] = 0.0
            total += float(amount)
            self.log['customer payment'] += 1
        for r in rows:
            if total and r['FCODE'] == code:
                take = min(float(r['AMOUNT'] or 0), total)
                r['AMOUNT'] = float(r['AMOUNT'] or 0) - take
                total -= take
        return [r for r in rows if float(r['AMOUNT'] or 0) or float(r.get('BAMOUNT') or 0)]

    def _match_oldest(self, payment):
        """What the accountant does with "Outstanding credits": the oldest
        open invoices of that customer first."""
        pay_line = payment.move_id.line_ids.filtered(
            lambda l: l.account_id.account_type == 'asset_receivable' and not l.reconciled)
        if not pay_line:
            return
        open_lines = self.env['account.move.line'].search([
            ('partner_id', '=', payment.partner_id.commercial_partner_id.id),
            ('account_id', '=', pay_line.account_id.id), ('reconciled', '=', False),
            ('parent_state', '=', 'posted'), ('balance', '>', 0)], order='date, id')
        for line in open_lines:
            if pay_line.reconciled:
                break
            (pay_line | line).reconcile()

    def undo_close(self, date_from, date_to):
        """Remove what close_month made for the period (invoices from notes,
        standard invoices, collections, entries) so it can be run again. The
        notes and the cash sales stay."""
        Move = self.env['account.move'].with_context(force_delete=True)
        moves = Move.search([('x_bas_key', '!=', False), ('x_bas_key', 'not like', 'OPENING%'),
                             ('date', '>=', date_from), ('date', '<', date_to)])
        lines = moves.line_ids
        (lines | lines.matched_debit_ids.debit_move_id | lines.matched_credit_ids.credit_move_id) \
            .remove_move_reconcile() if lines else None
        payments = self.env['account.payment'].search([('move_id', 'in', moves.ids)])
        if payments:
            payments.action_draft()
            payments.with_context(force_delete=True).unlink()
        rest = Move.browse(moves.exists().ids)
        rest.filtered(lambda m: m.state == 'posted').button_draft()
        rest.unlink()
        self.env.cr.execute("""DELETE FROM ksw_replay_problem WHERE step <> 'note'""")
        self.commit()
        return len(moves)

    # ------------------------------------------------------------------
    # around the run: the year-end close came after 1 January
    # ------------------------------------------------------------------
    def open_period(self):
        self.company.sudo().write({'fiscalyear_lock_date': False})
        self.commit()

    def finish(self):
        self.at(datetime(2026, 10, 1, 6, 0))
        self.company.sudo().write({'fiscalyear_lock_date': '2025-12-31'})
        self.env.cr.execute("SELECT to_regclass('ksw_replay_reactivated')")
        if self.env.cr.fetchone()[0]:
            self.env.cr.execute('SELECT employee_id FROM ksw_replay_reactivated')
            ids = [r[0] for r in self.env.cr.fetchall()]
            self.env['hr.employee'].sudo().browse(ids).write({'active': False})
        self.env.cr.execute("SELECT to_regclass('ksw_replay_reactivated_vehicle')")
        if self.env.cr.fetchone()[0]:
            self.env.cr.execute('SELECT vehicle_id FROM ksw_replay_reactivated_vehicle')
            self.env['ksw.fleet.vehicle'].sudo().browse(
                [r[0] for r in self.env.cr.fetchall()]).write({'active': False})
        for ptype in self.env['stock.picking.type'].search([('x_is_water_delivery', '=', True)]):
            ptype.x_location_rule = 'enforce'
        self.commit()

    # ------------------------------------------------------------------
    # the month, in order
    # ------------------------------------------------------------------
    def close_month(self, date_from, date_to):
        """Invoices, collections and entries of the period, in BAS order, so
        a receipt only ever meets the invoices that existed on its day."""
        import os
        hold = os.path.expanduser('~/native_replay_logs/hold_close')
        while os.path.exists(hold) and not self.env.context.get('ignore_hold'):
            time.sleep(60)
        self.issue_skipped(date_from, date_to)
        jobs = self.month_end_invoices(date_from, date_to) + self.vouchers(date_from, date_to)
        for _when, key, fn, args in sorted(jobs, key=lambda j: (j[0], j[1])):
            self.run_one(key, fn, *args)
        return dict(self.log)


def _chunks(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]

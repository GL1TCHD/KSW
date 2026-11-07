"""Accounting for the KSWCO parallel run (Nov 2026): flows only, no BAS history.

Production gets what odoo_bas_native was built on, without its transactions:
BAS's chart, cost centres and customer/supplier accounts, journals and VAT as
dev has them, and the water branches' posting settings. Nothing is posted and
no opening balance is loaded: from the first day, what people do in Odoo is
compared with what BAS shows for the same day.

Run after: -u account -i l10n_sa (then try_loading('sa') on a company emptied
of its demo entries) and -i KSW_accounting_ux,KSW_client_multiplier.
dev_config.json is exported from odoo_dev (the reference configuration).

    >>> exec(open('custom_addons/KSW/KSW_bas_gl_import/scripts/parallel_run_setup.py').read())
    >>> S = ParallelSetup(env, '/path/dev_config.json'); S.run(); env.cr.commit()
"""
import json
import logging

from odoo import Command

_logger = logging.getLogger('parallel_run_setup')

GUARD_DBS = ('kswco_rehearsal', 'KSWCO')
# Branch posting settings, as odoo_bas_native set them from BAS's 600 notes:
# the till BAS debits on cash notes, the sales account it credits most.
BRANCHES = {
    '120': {'till': '1201020001', 'sales': '4101010001', 'cost_centre': '1200040001'},
    '172': {'till': '1201020010', 'sales': '4101140003', 'cost_centre': '1200020001'},
}


class ParallelSetup:

    def __init__(self, env, config_path):
        assert env.cr.dbname in GUARD_DBS, env.cr.dbname
        self.env = env
        self.co = env['res.company'].browse(1)
        self.cfg = json.load(open(config_path))
        self.log = []

    def acc(self, code):
        return self.env['account.account'].with_context(active_test=False).search(
            [('code', '=', code), ('company_ids', 'in', self.co.id)], limit=1) if code else self.env['account.account']

    def say(self, *parts):
        self.log.append(' '.join(str(p) for p in parts))
        _logger.info(self.log[-1])

    # ------------------------------------------------------------------ BAS master data
    def bas_master(self):
        I = self.env['ksw.bas.gl.import'].with_company(self.co)
        self.say('chart', I.action_import_chart(self.co))
        self.say('names', I.action_sync_names(self.co))
        self.say('cost centres', I.action_import_cost_centres(self.co))
        I._ksw_split_cost_centre_plans()
        self.say('partners', I.action_import_partners(self.co))
        self.say('partner accounts', I.action_map_partner_accounts(self.co))

    # ------------------------------------------------------------------ VAT
    def taxes(self):
        Tax = self.env['account.tax'].with_context(active_test=False)
        for t in self.cfg['taxes']:
            tax = Tax.search([('company_id', '=', self.co.id), ('type_tax_use', '=', t['use']),
                              ('amount', '=', t['amount'])]).filtered(
                lambda x: x.with_context(lang='en_US').name == t['name'])[:1]
            if not tax:
                continue
            for lines, wanted in ((tax.invoice_repartition_line_ids, t['inv']),
                                  (tax.refund_repartition_line_ids, t['ref'])):
                for line, (rtype, code) in zip(lines.sorted('sequence'), wanted):
                    account = self.acc(code)
                    if line.repartition_type == rtype and line.account_id != account and (account or not code):
                        line.account_id = account
            if tax.active != t['active']:
                tax.active = t['active']
        t15 = self.sale_tax15 = Tax.search([('company_id', '=', self.co.id), ('type_tax_use', '=', 'sale'),
                          ('amount', '=', 15.0), ('active', '=', True)]).filtered(
            lambda x: x.with_context(lang='en_US').name == '15%')[:1]
        self.say('VAT on BAS accounts')

    # ------------------------------------------------------------------ water items
    def water_products(self):
        """BAS's water and ice items as dev has them (ITM10 codes), with VAT 15%.

        Existing ones keep their record and get their BAS code; a BAS item the
        branches sell and KSWCO never had (11006, 6 m water) is created."""
        env = self.env
        Categ = env['product.category']
        categ = Categ.search([('name', '=', self.cfg['water_category']['name'])], limit=1) \
            or Categ.create({'name': self.cfg['water_category']['name']})
        Template = env['product.template'].with_context(active_test=False)
        Uom = env['uom.uom'].with_context(lang='en_US')
        made = linked = 0
        for d in self.cfg['water_products']:
            tmpl = Template.search([('default_code', '=', d['code'])], limit=1)
            if not tmpl:
                uom = Uom.search([('name', '=', d['uom'])], limit=1)
                tmpl = Template.create({
                    'name': d['name_en'], 'default_code': d['code'], 'type': d['type'],
                    'sale_ok': True, 'purchase_ok': False, 'invoice_policy': d['invoice_policy'],
                    'uom_id': uom.id, 'categ_id': categ.id,
                })
                if d['name_ar'] and d['name_ar'] != d['name_en']:
                    tmpl.update_field_translations('name', {'ar_001': d['name_ar']})
                made += 1
            vals = {}
            if tmpl.categ_id != categ:
                vals['categ_id'] = categ.id
            if self.sale_tax15 not in tmpl.taxes_id:
                vals['taxes_id'] = [Command.link(self.sale_tax15.id)]
            if vals:
                tmpl.write(vals)
            for variant in tmpl.product_variant_ids.filtered(lambda v: v.x_bas_code != d['code']):
                variant.x_bas_code = d['code']
                linked += 1
        self.say('water items:', made, 'created,', linked, 'linked to their BAS code')

    # ------------------------------------------------------------------ journals
    def journals(self):
        Journal = self.env['account.journal'].with_context(active_test=False)
        mine = {j.code: j for j in Journal.search([('company_id', '=', self.co.id)])}
        wanted = {j['code'] for j in self.cfg['journals'] if j['active']}
        made = 0
        for j in self.cfg['journals']:
            if not j['active']:
                continue
            journal = mine.get(j['code'])
            default = self.acc(j['default_account'])
            vals = {'name': j['name_en']}
            if default:
                vals['default_account_id'] = default.id
            if not journal:
                if j['type'] in ('bank', 'cash') and not default:
                    continue
                journal = Journal.create(dict(vals, code=j['code'], type=j['type'], company_id=self.co.id))
                made += 1
            else:
                journal.write(vals)
            if j['name_ar'] and j['name_ar'] != j['name_en']:
                journal.update_field_translations('name', {'ar_001': j['name_ar']})
            if j['type'] in ('bank', 'cash') and default:
                # Payments land straight in the BAS bank or till (dev's setup),
                # always the journal's own account.
                for line in journal.inbound_payment_method_line_ids | journal.outbound_payment_method_line_ids:
                    line.payment_account_id = default
        Move = self.env['account.move']
        for code, journal in mine.items():
            if code not in wanted and journal.active and not Move.search_count([('journal_id', '=', journal.id)]):
                journal.active = False
                self.say('journal archived', code)
        self.say('journals:', made, 'created')

    # ------------------------------------------------------------------ company
    def company(self):
        vals = {}
        for field, code in self.cfg['company_accounts'].items():
            if field in self.co._fields:
                vals[field] = self.acc(code).id if code else False
        Journal = self.env['account.journal']
        for field, code in self.cfg['company_journals'].items():
            if field in self.co._fields and code:
                journal = Journal.search([('company_id', '=', self.co.id), ('code', '=', code)], limit=1)
                if journal:
                    vals[field] = journal.id
        self.co.write(vals)
        payable = self.acc(self.cfg['default_payable'])
        if payable:
            self.env['ir.default'].set('res.partner', 'property_account_payable_id', payable.id,
                                       company_id=self.co.id)
        self.say('company defaults set:', sorted(k for k, v in vals.items() if v))

    def archive_template_accounts(self):
        """The l10n_sa accounts dev archived (BAS's chart only, Sep 2026)."""
        Line = self.env['account.move.line']
        done = 0
        for code in self.cfg['archived_accounts']:
            account = self.acc(code)
            if not account or not account.active or Line.search_count([('account_id', '=', account.id)], limit=1):
                continue
            try:
                with self.env.cr.savepoint():
                    account.active = False
                    account.flush_recordset()
                done += 1
            except Exception as e:     # still a default somewhere: it stays
                self.say('kept', code, str(e).splitlines()[0][:120])
        self.say('template accounts archived:', done)

    # ------------------------------------------------------------------ water
    def water_branches(self):
        env = self.env
        Analytic = env['account.analytic.account']
        Journal = env['account.journal']
        for code, b in BRANCHES.items():
            wh = env['stock.warehouse'].search([('x_branch_code', '=', code)], limit=1)
            if not wh:
                self.say('branch', code, 'has no warehouse')
                continue
            till = self.acc(b['till'])
            journal = Journal.search([('company_id', '=', self.co.id), ('type', '=', 'cash'),
                                      ('default_account_id', '=', till.id)], limit=1)
            cc = Analytic.search([('code', '=', b['cost_centre'])], limit=1) if b['cost_centre'] else None
            if not cc:
                name = env['ksw.bas.branch'].search([('code', '=', code)], limit=1).with_context(lang='ar_001').name
                plan = env['account.analytic.plan'].search([('name', '=', 'Branch Cost Centre')], limit=1)
                cc = Analytic.search([('plan_id', 'child_of', plan.id)]).filtered(
                    lambda a: name and name in (a.with_context(lang='ar_001').name or ''))[:1]
            wh.write({
                'x_sales_account_id': self.acc(b['sales']).id,
                'x_cash_journal_id': journal.id or False,
                'x_analytic_account_id': cc.id if cc else False,
            })
            ptype = wh.x_water_picking_type_ids[:1]
            if ptype and not wh.x_cash_partner_id:
                ptype.action_create_cash_customer()
            if wh.x_cash_partner_id:
                self._own_receivable(wh.x_cash_partner_id)
            self.say('branch', code, wh.code, 'sales', b['sales'], 'till', journal.code,
                     'cost centre', cc.code if cc else None, 'cash customer', wh.x_cash_partner_id.name)
        self.revenue_rules()

    def _own_receivable(self, partner):
        """A cash customer is its own 1203 account, as every BAS customer is."""
        partner = partner.with_company(self.co)
        if partner.property_account_receivable_id.code.startswith('120301'):
            return
        Account = self.env['account.account']
        codes = sorted(int(a.code) for a in Account.search([('code', '=like', '120301___')]))
        account = Account.create({
            'code': str((codes[-1] if codes else 120301000) + 1), 'name': partner.name,
            'account_type': 'asset_receivable', 'reconcile': True,
        })
        partner.property_account_receivable_id = account

    def revenue_rules(self):
        """BAS's own split: per branch and item, the sales account BAS credits most."""
        self.env['ksw.water.revenue.account'].sudo().action_import_from_bas()
        Rule = self.env['ksw.water.revenue.account']
        self.say('revenue rules:', {code: Rule.search_count([('branch_code', '=', code)]) for code in BRANCHES})

    def run(self):
        self.bas_master()
        self.taxes()
        self.water_products()
        self.journals()
        self.company()
        self.archive_template_accounts()
        self.water_branches()
        return self.log

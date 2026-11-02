from datetime import date

from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestFinancialStatements(TransactionCase):
    """Balance Sheet and Cash Flow tie out, and agree with the P&L."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Account = cls.env['account.account']
        cls.bank = Account.create({'name': 'KSW test bank', 'code': '1202999901', 'account_type': 'asset_cash'})
        cls.customer = Account.create({'name': 'KSW test customer', 'code': '1203999901', 'account_type': 'asset_receivable', 'reconcile': True})
        cls.revenue = Account.create({'name': 'KSW test revenue', 'code': '4101999901', 'account_type': 'income'})
        cls.expense = Account.create({'name': 'KSW test expense', 'code': '3202999901', 'account_type': 'expense'})
        cls.loan = Account.create({'name': 'KSW test loan', 'code': '2102999901', 'account_type': 'liability_current'})
        journal = cls.env['account.journal'].search(
            [('type', '=', 'general'), ('company_id', '=', cls.env.company.id)], limit=1)
        # A year with no real (BAS-imported) entries and no BAS depreciation
        # basis, so the figures of that day are exactly these four entries.
        cls.day = day = date(2030, 3, 15)
        cls.env['account.move'].create([{
            'journal_id': journal.id, 'date': day, 'line_ids': [
                (0, 0, {'account_id': debit.id, 'debit': amount}),
                (0, 0, {'account_id': credit.id, 'credit': amount}),
            ]} for debit, credit, amount in (
                (cls.customer, cls.revenue, 11500.0),   # sale on credit
                (cls.bank, cls.customer, 7000.0),       # part collected
                (cls.expense, cls.bank, 2500.0),        # expense paid
                (cls.bank, cls.loan, 50000.0),          # loan drawn
            )]).action_post()

    def _kpis(self, action):
        data = self.env['mis.report.instance'].browse(action['res_id']).compute()
        out = {}
        for row in data['body']:
            name = (row['cells'][0].get('val_c') or '').split(' = ')[0]
            out[name] = [c.get('val') or 0.0 for c in row['cells']]
        return out

    def test_balance_sheet_balances_and_agrees_with_pl(self):
        bs = self._kpis(self.env['ksw.bs.wizard'].create(
            {'date_to': date(2026, 6, 30), 'comparison': 'prev_year'}).action_open())
        pl = self._kpis(self.env['ksw.pl.wizard'].create(
            {'date_from': date(2026, 1, 1), 'date_to': date(2026, 6, 30)}).action_open())
        for col in range(2):
            self.assertAlmostEqual(bs['check'][col], 0.0, places=2)
        self.assertAlmostEqual(bs['eq_result'][0], pl['net'][0], places=2)

    def test_cash_flow_ties_to_cash_movement(self):
        cf = self._kpis(self.env['ksw.cf.wizard'].create(
            {'date_from': date(2026, 1, 1), 'date_to': date(2026, 6, 30)}).action_open())
        self.assertAlmostEqual(cf['other'][0], 0.0, places=2,
                               msg='a movement escaped every cash-flow line')
        self.assertAlmostEqual(cf['change'][0], cf['cash_close'][0] - cf['cash_open'][0], places=2)

    def test_cash_flow_classifies_the_test_entries(self):
        """Each test entry lands on the line its accounts belong to."""
        cf = self._kpis(self.env['ksw.cf.wizard'].create(
            {'date_from': self.day, 'date_to': self.day}).action_open())
        self.assertAlmostEqual(cf['net'][0], 11500.0 - 2500.0, places=2)
        self.assertAlmostEqual(cf['wc_1203'][0], -4500.0, places=2)   # sold 11,500, collected 7,000
        self.assertAlmostEqual(cf['op'][0], 4500.0, places=2)
        self.assertAlmostEqual(cf['fin_2102'][0], 50000.0, places=2)
        self.assertAlmostEqual(cf['other'][0], 0.0, places=2)
        self.assertAlmostEqual(cf['change'][0], 7000.0 - 2500.0 + 50000.0, places=2)

    def test_balance_sheet_on_test_year(self):
        bs = self._kpis(self.env['ksw.bs.wizard'].create(
            {'date_to': self.day, 'comparison': 'prev_year'}).action_open())
        self.assertAlmostEqual(bs['check'][0], 0.0, places=2)
        # Year result is this year's only; earlier years are "not yet closed".
        self.assertAlmostEqual(bs['eq_result'][0], 9000.0, places=2)
        self.assertAlmostEqual(bs['eq_prior'][0], bs['eq_prior'][1] + bs['eq_result'][1], places=2)

    def test_balance_sheet_monthly_comparison_column(self):
        wiz = self.env['ksw.bs.wizard'].create({'date_to': date(2026, 3, 15), 'comparison': 'prev_month'})
        self.assertEqual([(p['manual_date_from'], p['manual_date_to']) for p in wiz._periods()], [
            (date(2026, 1, 1), date(2026, 3, 15)),
            (date(2026, 1, 1), date(2026, 2, 28)),
        ])


@tagged('post_install', '-at_install')
class TestPartnerStatementFromMenu(TransactionCase):
    def test_statement_uses_chosen_partners_without_active_ids(self):
        partner = self.env['res.partner'].create({'name': 'KSW statement test', 'customer_rank': 1})
        for model in ('activity.statement.wizard', 'outstanding.statement.wizard'):
            wiz = self.env[model].create({'partner_ids': [(6, 0, partner.ids)]})
            self.assertEqual(wiz._prepare_statement()['partner_ids'], partner.ids)

    def test_action_menu_still_prefills_from_selection(self):
        partners = self.env['res.partner'].create([{'name': 'A'}, {'name': 'B'}])
        wiz = self.env['activity.statement.wizard'].with_context(
            active_model='res.partner', active_ids=partners.ids).create({})
        self.assertEqual(wiz.partner_ids, partners)


@tagged('post_install', '-at_install')
class TestPartnerStatementBothSides(TransactionCase):
    """A partner who is both client and supplier gets one netted statement."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Account = cls.env['account.account']
        cls.partner = cls.env['res.partner'].create(
            {'name': 'KSW both sides', 'customer_rank': 1, 'supplier_rank': 1})
        receivable = Account.create({'name': 'KSW test AR', 'code': '1203999902',
                                     'account_type': 'asset_receivable', 'reconcile': True})
        payable = Account.create({'name': 'KSW test AP', 'code': '2101999902',
                                  'account_type': 'liability_payable', 'reconcile': True})
        income = Account.create({'name': 'KSW test inc', 'code': '4101999902', 'account_type': 'income'})
        journal = cls.env['account.journal'].search(
            [('type', '=', 'general'), ('company_id', '=', cls.env.company.id)], limit=1)
        cls.first_day = date(2030, 2, 10)
        cls.env['account.move'].create([{
            'journal_id': journal.id, 'date': day, 'line_ids': [
                (0, 0, {'account_id': debit.id, 'debit': amount, 'partner_id': cls.partner.id}),
                (0, 0, {'account_id': credit.id, 'credit': amount, 'partner_id': cls.partner.id}),
            ]} for day, debit, credit, amount in (
                (cls.first_day, receivable, income, 1000.0),     # we sold to him
                (date(2030, 3, 1), income, payable, 300.0),      # we bought from him
            )]).action_post()
        cls.currency = cls.env.company.currency_id

    def _statement(self, model, report, **vals):
        vals = dict({'partner_ids': [(6, 0, self.partner.ids)], 'date_end': date(2030, 3, 15),
                     'x_date_range': 'custom'}, **vals)
        if 'date_start' in self.env[model]._fields:
            vals['date_start'] = date(2030, 1, 1)
        wiz = self.env[model].create(vals)
        data = wiz._prepare_statement()
        res = self.env[report]._get_report_values(self.partner.ids, data)
        return res['data'][self.partner.id]['currencies'][self.currency.id], res

    def test_both_is_the_default(self):
        self.assertEqual(self.env['activity.statement.wizard'].create({}).account_type, 'all')

    def test_activity_statement_nets_both_sides(self):
        report = 'report.partner_statement.activity_statement'
        for account_type, due in (('all', 700.0), ('asset_receivable', 1000.0),
                                  ('liability_payable', -300.0)):
            cur, res = self._statement('activity.statement.wizard', report, account_type=account_type)
            self.assertAlmostEqual(cur['amount_due'], due, msg=account_type)
        cur, res = self._statement('activity.statement.wizard', report, account_type='all')
        self.assertEqual(len(cur['lines']), 2)
        title = res['get_title'](self.partner, account_type='all', starting_date='a',
                                 ending_date='b', currency='SAR')
        self.assertNotIn('Supplier', title)

    def test_outstanding_and_detailed_statements_net_both_sides(self):
        cur, _res = self._statement('outstanding.statement.wizard',
                                    'report.partner_statement.outstanding_statement')
        self.assertAlmostEqual(cur['amount_due'], 700.0)
        cur, _res = self._statement('detailed.activity.statement.wizard',
                                    'report.partner_statement.detailed_activity_statement')
        self.assertAlmostEqual(cur['ending_lines'][-1]['balance'], 700.0)

    def test_period_choices(self):
        Wizard = self.env['activity.statement.wizard']
        base = {'partner_ids': [(6, 0, self.partner.ids)], 'date_end': date(2030, 3, 15)}
        self.assertEqual(Wizard.create(dict(base, x_date_range='history'))
                         ._prepare_statement()['date_start'], self.first_day)
        year_start = self.env.company.compute_fiscalyear_dates(date(2030, 3, 15))['date_from']
        self.assertEqual(Wizard.create(dict(base, x_date_range='year'))
                         ._prepare_statement()['date_start'], year_start)
        self.assertEqual(Wizard.create(dict(base, x_date_range='custom', date_start=date(2030, 3, 1)))
                         ._prepare_statement()['date_start'], date(2030, 3, 1))

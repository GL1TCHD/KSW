from datetime import date

from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestAssetRemovalAccounts(TransactionCase):
    """Selling a BAS asset retires it from its OWN 19xx/24xx accounts."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Account = cls.env['account.account']

        def account(code, account_type):
            return Account.create({'code': code, 'name': 'T' + code,
                                   'account_type': account_type, 'x_bas_code': code})

        cls.group_cost = account('1906999901', 'asset_fixed')     # profile's leaf
        cls.group_accum = account('2406999901', 'asset_fixed')
        cls.own_cost = account('1906999902', 'asset_fixed')       # this asset's
        cls.own_accum = account('2406999902', 'asset_fixed')
        cls.expense = account('3306999902', 'expense_depreciation')
        cls.gain_loss = account('4201999903', 'income_other')
        cls.proceeds = account('1209999904', 'asset_current')
        journal = cls.env['account.journal'].create(
            {'name': 'T Depr', 'code': 'TDPR', 'type': 'general'})
        profile = cls.env['account.asset.profile'].create({
            'name': 'T Vehicles', 'journal_id': journal.id,
            'account_asset_id': cls.group_cost.id,
            'account_depreciation_id': cls.group_accum.id,
            'account_expense_depreciation_id': cls.expense.id,
            'method': 'linear', 'method_time': 'number', 'method_number': 60,
            'method_period': 'month',
        })
        cls.asset = cls.env['account.asset'].create({
            'name': 'T truck', 'code': cls.own_cost.x_bas_code,
            'profile_id': profile.id, 'purchase_value': 120000.0,
            'date_start': date(2025, 1, 1), 'prorata': True,
            'x_bas_depreciation_account_id': cls.own_accum.id,
        })
        cls.asset.validate()
        cls.asset.compute_depreciation_board()
        cls.asset.depreciation_line_ids.filtered(
            lambda l: l.type == 'depreciate' and not l.init_entry
            and l.line_date <= date(2026, 10, 31)).sorted('line_date').create_move()

    def _sell(self, sale_value):
        action = self.asset.remove()
        wizard = self.env['account.asset.remove'].with_context(action['context']).create({
            'date_remove': date(2026, 11, 15), 'sale_value': sale_value,
            'account_sale_id': self.proceeds.id,
            'account_plus_value_id': self.gain_loss.id,
            'account_min_value_id': self.gain_loss.id,
            'posting_regime': 'gain_loss_on_sale',
        })
        result = wizard.remove()
        return self.env['account.move'].search(result['domain'])

    def test_sale_hits_own_accounts(self):
        move = self._sell(50000.0)
        by_account = {l.account_id: l.balance for l in move.line_ids}
        self.assertEqual(self.asset.state, 'removed')
        self.assertAlmostEqual(by_account[self.own_cost], -120000.0)
        self.assertAlmostEqual(by_account[self.own_accum], 44933.33, places=2)
        self.assertAlmostEqual(by_account[self.proceeds], 50000.0)
        self.assertAlmostEqual(by_account[self.gain_loss], 25066.67, places=2)
        self.assertNotIn(self.group_cost, by_account)
        self.assertNotIn(self.group_accum, by_account)
        self.assertAlmostEqual(sum(move.line_ids.mapped('balance')), 0.0)

    def test_chosen_account_is_never_remapped(self):
        """A gain/loss account that happens to be a profile account stays put."""
        self.gain_loss = self.group_accum
        move = self._sell(50000.0)
        self.assertAlmostEqual(
            sum(move.line_ids.filtered(lambda l: l.account_id == self.group_accum)
                .mapped('balance')), 25066.67, places=2)

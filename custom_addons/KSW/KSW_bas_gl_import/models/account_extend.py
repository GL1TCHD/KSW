from odoo import fields, models


class AccountGroup(models.Model):
    _inherit = 'account.group'

    x_bas_code = fields.Char(
        string='BAS Code', index=True, copy=False,
        help="Account code in BAS (COD10.DCODE1) for hierarchy levels 1-4.")


class AccountAccount(models.Model):
    _inherit = 'account.account'

    x_bas_code = fields.Char(
        string='BAS Code', index=True, copy=False,
        help="Leaf account code in BAS (COD10.DCODE1, DLEVEL=5).")
    x_bas_name_ar = fields.Char(string='BAS Name (Arabic)')
    x_bas_name_en = fields.Char(string='BAS Name (English)')


class AccountAnalyticPlan(models.Model):
    _inherit = 'account.analytic.plan'

    # Set on the root "Cost Centres" plan ('WREF10') and on each sub-plan made
    # from a BAS group node (WREF10.CODE).  Empty on plans made in Odoo.
    x_bas_code = fields.Char(string='BAS Code', index=True, copy=False)

    _x_bas_code_uniq = models.Constraint(
        'UNIQUE (x_bas_code)', 'A plan for this BAS cost centre already exists.')


class AccountAnalyticAccount(models.Model):
    _inherit = 'account.analytic.account'

    # Provenance only.  BAS seeds the cost centres once; after that they are
    # maintained in Odoo, so an account created here simply has none.
    x_bas_code = fields.Char(
        string='BAS Cost Centre', index=True, copy=False, readonly=True,
        help="WREF10.CODE of the BAS cost centre this account was imported from.")

    # Per root plan: a driver is an account in "Cost Centres" AND in
    # "Employee Cost Centre", both carrying the same BAS code.
    _x_bas_code_uniq = models.Constraint(
        'UNIQUE (x_bas_code, root_plan_id)',
        'An analytic account for this BAS cost centre already exists in this plan.')


class AccountMove(models.Model):
    _name = 'account.move'
    _inherit = ['account.move', 'ksw.bas.branch.mixin']

    # Idempotency key: FTYPE/FTYPE2/CODE2/NUMBER1 of the BAS voucher.
    # Unique so a re-run updates nothing and duplicates nothing.
    x_bas_key = fields.Char(string='BAS Voucher Key', index=True, copy=False)
    x_bas_ftype = fields.Char(string='BAS FTYPE', copy=False)

    # Odoo 19 dropped ``_sql_constraints`` -- it is silently ignored and only
    # logs "Model attribute '_sql_constraints' is no longer supported".  The
    # unique index was therefore NEVER created on the first pass; idempotency
    # held only because action_import also de-duplicates in Python.
    _x_bas_key_uniq = models.Constraint(
        'UNIQUE (x_bas_key)',
        'A journal entry for this BAS voucher already exists.')


class ResPartner(models.Model):
    _inherit = 'res.partner'

    # A BAS "customer"/"supplier" is a leaf GL account code, so it is the
    # natural key back to BAS. Stored as the contact's Reference, shared with
    # the customer sync (KSW_ext_sync), so one BAS account is one contact.
    x_bas_code = fields.Char(related='ref', readonly=False, string='BAS Account Code')
    x_bas_name_ar = fields.Char(string='BAS Name (Arabic)')
    # Raw TAX_ID as BAS holds it -- kept even when it fails the ZATCA format,
    # so invalid registrations are visible rather than silently discarded.
    x_bas_vat = fields.Char(string='BAS TAX_ID (raw)')


class ProductProduct(models.Model):
    _inherit = 'product.product'

    x_bas_code = fields.Char(string='BAS Item Code', index=True, copy=False)


class StockPicking(models.Model):
    _name = 'stock.picking'
    _inherit = ['stock.picking', 'ksw.bas.branch.mixin']

    x_bas_key = fields.Char(string='BAS Delivery Note', index=True, copy=False)

    _x_bas_key_uniq = models.Constraint(
        'UNIQUE (x_bas_key)', 'This BAS delivery note is already imported.')


class AccountAsset(models.Model):
    _inherit = 'account.asset'

    # BAS names a DISTINCT depreciation-expense account per asset
    # (``COD10.DDEP_CODE``: 1906020135 -> 3306020121, 733 assets, 733 codes),
    # and the accumulated side is the 24xx suffix pair.  Odoo's asset profile
    # carries one pair of accounts for the whole profile, so without these the
    # entire register would post to one arbitrary 33xx leaf and BAS's own
    # per-asset trial balance rows could never be reproduced.
    x_bas_expense_account_id = fields.Many2one(
        'account.account', string='BAS Depreciation Expense Account',
        help="COD10.DDEP_CODE for this asset. Overrides the profile.")
    x_bas_depreciation_account_id = fields.Many2one(
        'account.account', string='BAS Accumulated Depreciation Account',
        help="The 24xx counterpart for this asset. Overrides the profile.")
    x_bas_annual_rate = fields.Float(
        string='BAS Annual Rate (%)', digits=(5, 3),
        help="COD10.DDEP_PER -- straight-line percentage of cost, as BAS holds it.")


class AccountAssetLine(models.Model):
    _inherit = 'account.asset.line'

    def _setup_move_line_data(self, depreciation_date, account, ml_type, move):
        """Post to the asset's OWN BAS accounts when it has them.

        ``create_move`` reads both accounts off ``asset.profile_id``; this is
        the extension point it funnels through, so overriding here keeps the
        6 group profiles and still lands every charge on the leaf account BAS
        uses.  Falls back to whatever the profile said for any asset that has
        no BAS mapping.
        """
        asset = self.asset_id
        if ml_type == 'expense' and asset.x_bas_expense_account_id:
            account = asset.x_bas_expense_account_id
        elif ml_type == 'depreciation' and asset.x_bas_depreciation_account_id:
            account = asset.x_bas_depreciation_account_id
        return super()._setup_move_line_data(
            depreciation_date, account, ml_type, move)


class AccountAssetRemove(models.TransientModel):
    _inherit = 'account.asset.remove'

    def _get_removal_data(self, asset, residual_value):
        """Retire the asset from its OWN BAS accounts, not the profile's.

        The removal entry reverses cost and accumulated depreciation off
        ``profile.account_asset_id`` / ``account_depreciation_id`` -- for a BAS
        profile that is the group's first leaf (one specific vehicle), so
        selling any other vehicle would credit that one's cost account.  Cost
        sits on the 19xx account whose BAS code is the asset's ``code``; the
        accumulated side is the same per-asset 24xx account depreciation posts to.
        """
        commands = super()._get_removal_data(asset, residual_value)
        profile = asset.profile_id
        cost_account = asset.code and self.env['account.account'].search(
            [('x_bas_code', '=', asset.code)], limit=1)
        remap = {}
        if cost_account:
            remap[profile.account_asset_id.id] = cost_account.id
        if asset.x_bas_depreciation_account_id:
            remap[profile.account_depreciation_id.id] = \
                asset.x_bas_depreciation_account_id.id
        # super() emits the reversal lines first -- accumulated depreciation
        # (only when there is any) then cost -- and only after them the
        # proceeds / gain / loss / residual lines on accounts the user picked in
        # this wizard, which may coincide with a profile account; never move those.
        reversal = 2 if asset.depreciation_base - residual_value else 1
        for command in commands[:reversal]:
            vals = command[2]
            if vals.get('account_id') in remap:
                vals['account_id'] = remap[vals['account_id']]
        return commands

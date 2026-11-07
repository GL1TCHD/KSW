from odoo import api, models


class AccountAccount(models.Model):
    _inherit = 'account.account'

    @api.model
    def ksw_get_account_tree(self):
        """The current company's chart as one flat list, for the tree page.

        Groups (BAS levels 1-4) carry their ``parent_id``; accounts carry the
        group their code falls in (core's ``group_id``, resolved by prefix).
        The page builds the hierarchy and sums balances upward itself, so one
        call is all it needs: 4,700 rows, expanded and searched client-side.
        Read as the user, so the access rules decide what is listed.
        """
        company = self.env.company
        groups = self.env['account.group'].search_read(
            [('company_id', '=', company.id)],
            ['code_prefix_start', 'name', 'parent_id'])
        accounts = self.with_company(company).search_read(
            [('company_ids', 'in', company.id)],
            ['code', 'name', 'account_type', 'group_id', 'reconcile'])

        balances = {}
        Line = self.env['account.move.line']
        if Line.has_access('read'):
            for account, balance in Line._read_group(
                [('parent_state', '=', 'posted'), ('company_id', '=', company.id)],
                ['account_id'], ['balance:sum'],
            ):
                balances[account.id] = balance

        types = dict(self._fields['account_type']._description_selection(self.env))
        return {
            'company': company.name,
            'currency': {
                'symbol': company.currency_id.symbol,
                'position': company.currency_id.position,
                'digits': company.currency_id.decimal_places,
            },
            'show_balance': Line.has_access('read'),
            'groups': [{
                'id': g['id'],
                'code': g['code_prefix_start'] or '',
                'name': g['name'],
                'parent_id': g['parent_id'] and g['parent_id'][0],
            } for g in groups],
            'accounts': [{
                'id': a['id'],
                'code': a['code'] or '',
                'name': a['name'],
                'type': types.get(a['account_type'], ''),
                'group_id': a['group_id'] and a['group_id'][0],
                'reconcile': a['reconcile'],
                'balance': balances.get(a['id'], 0.0),
            } for a in accounts],
        }

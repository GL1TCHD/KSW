from odoo import models

from ..wizard.partner_statement import BOTH

RECEIVABLE = 'asset_receivable'
# What the OCA queries render for ``aa.account_type = %(account_type)s``
_ONE_SIDE = "aa.account_type = 'asset_receivable'"
_BOTH_SIDES = "aa.account_type IN ('asset_receivable', 'liability_payable')"


def _both_sides(sql):
    """Widen one OCA statement query from receivable to both sides.

    Every partner_statement query filters ``aa.account_type`` with ``=``
    exactly once; the amounts are already debit - credit on either side, so
    the union nets into one running balance (positive: the partner owes us).
    Raise rather than silently print a one-sided statement if OCA changes it.
    """
    if sql.count(_ONE_SIDE) != 1:
        raise ValueError('partner_statement query changed: cannot widen it to both sides')
    return sql.replace(_ONE_SIDE, _BOTH_SIDES)


def _title_kwargs(kwargs):
    # The OCA titles read "Supplier Statement" for anything not receivable.
    if kwargs.get('account_type') == BOTH:
        kwargs['account_type'] = RECEIVABLE
    return kwargs


class StatementCommon(models.AbstractModel):
    _inherit = 'statement.common'

    def _show_buckets_sql_q1(self, partners, date_end, account_type):
        if account_type != BOTH:
            return super()._show_buckets_sql_q1(partners, date_end, account_type)
        return _both_sides(super()._show_buckets_sql_q1(partners, date_end, RECEIVABLE))


class ActivityStatement(models.AbstractModel):
    # The detailed statement inherits these two from here.
    _inherit = 'report.partner_statement.activity_statement'

    def _get_title(self, partner, **kwargs):
        return super()._get_title(partner, **_title_kwargs(kwargs))

    def _initial_balance_sql_q1(self, partners, date_start, account_type):
        if account_type != BOTH:
            return super()._initial_balance_sql_q1(partners, date_start, account_type)
        return _both_sides(super()._initial_balance_sql_q1(partners, date_start, RECEIVABLE))

    def _display_activity_lines_sql_q1(self, partners, date_start, date_end, account_type):
        if account_type != BOTH:
            return super()._display_activity_lines_sql_q1(
                partners, date_start, date_end, account_type)
        return _both_sides(super()._display_activity_lines_sql_q1(
            partners, date_start, date_end, RECEIVABLE))


class OutstandingStatement(models.AbstractModel):
    _inherit = 'report.partner_statement.outstanding_statement'

    def _get_title(self, partner, **kwargs):
        return super()._get_title(partner, **_title_kwargs(kwargs))

    def _display_outstanding_lines_sql_q1(self, partners, date_end, account_type):
        if account_type != BOTH:
            return super()._display_outstanding_lines_sql_q1(partners, date_end, account_type)
        return _both_sides(super()._display_outstanding_lines_sql_q1(
            partners, date_end, RECEIVABLE))


class DetailedActivityStatement(models.AbstractModel):
    # OCA copies the outstanding query onto this class by assignment, so it
    # does not inherit the override above and needs its own.
    _inherit = 'report.partner_statement.detailed_activity_statement'

    def _get_title(self, partner, **kwargs):
        return super()._get_title(partner, **_title_kwargs(kwargs))

    def _display_outstanding_lines_sql_q1(self, partners, date_end, account_type):
        if account_type != BOTH:
            return super()._display_outstanding_lines_sql_q1(partners, date_end, account_type)
        return _both_sides(super()._display_outstanding_lines_sql_q1(
            partners, date_end, RECEIVABLE))

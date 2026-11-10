"""Data for the Helpdesk overview page (IT Team only).

Same layout and conventions as the KSW Workshop / ERP overview pages: the open
backlog and how old it is, what came in and what was closed in the period, how
long a ticket takes, who is working on what, what users think of it, and the
state of the IT asset register. Every figure carries the domain of the records
behind it, so a card always opens what it counts.

The period helpers are a copy of KSW_erp_dashboard's, not an import: that
module depends on Accounting and Inventory, which the Helpdesk must not pull in.
"""
from datetime import date, datetime, time, timedelta

import pytz
from dateutil.relativedelta import relativedelta

from odoo import api, fields, models
from odoo.exceptions import AccessError


def _period(period, today):
    """(start, end, previous start, previous end) for this / last month, quarter, year."""
    if period == 'last_month':
        start = today.replace(day=1) - relativedelta(months=1)
        end = today.replace(day=1) - timedelta(days=1)
        prev = start - relativedelta(months=1)
        return start, end, prev, start - timedelta(days=1)
    if period == 'quarter':
        q = (today.month - 1) // 3
        start = date(today.year, 3 * q + 1, 1)
        prev = start - relativedelta(months=3)
        return start, today, prev, start - timedelta(days=1)
    if period == 'year':
        start = date(today.year, 1, 1)
        return start, today, date(today.year - 1, 1, 1), date(today.year - 1, today.month, today.day)
    start = today.replace(day=1)
    prev = start - relativedelta(months=1)
    return start, today, prev, start - timedelta(days=1)


def _trend_buckets(period, today):
    """(unit, [(label, first day, last day)]): a month by day, a quarter by week, a year by month."""
    start, end, _ps, _pe = _period(period, today)
    buckets = []
    if period == 'year':
        month = start
        while month <= end:
            nxt = month + relativedelta(months=1)
            buckets.append((month.strftime('%Y-%m'), month, min(nxt - timedelta(days=1), end)))
            month = nxt
        return 'month', buckets
    step = 7 if period == 'quarter' else 1
    day = start
    while day <= end:
        last = min(day + timedelta(days=step - 1), end)
        buckets.append((day.strftime('%d/%m') if step > 1 else day.strftime('%d'), day, last))
        day = last + timedelta(days=1)
    return ('week' if step > 1 else 'day'), buckets


def _sum_by_bucket(buckets, by_day):
    return [sum(v for d, v in by_day.items() if a <= d <= b) for _l, a, b in buckets]


class HelpdeskDashboard(models.AbstractModel):
    _name = 'helpdesk.dashboard'
    _description = 'Helpdesk Overview'

    @api.model
    def _datetime_span(self, start, end, field):
        """Domain on a Datetime field for local days start..end (stored UTC; Riyadh is +3)."""
        tz = pytz.timezone(self.env.context.get('tz') or self.env.user.tz or 'UTC')

        def utc(day):
            return tz.localize(datetime.combine(day, time.min)).astimezone(pytz.utc).replace(tzinfo=None)
        return [(field, '>=', utc(start)), (field, '<', utc(end + timedelta(days=1)))]

    @api.model
    def get_dashboard_data(self, period='month'):
        # An employee's rule only reaches his own tickets, so his figures would
        # be silently partial: the page is the IT Team's.
        if not self.env.su and not self.env.user.has_group('KSW_helpdesk.group_helpdesk_agent'):
            raise AccessError(self.env._('The Helpdesk overview is for the IT Team.'))
        _ = self.env._
        today = fields.Date.context_today(self)
        now = fields.Datetime.now()
        start, end, pstart, pend = _period(period, today)
        Ticket = self.env['helpdesk.ticket']

        def count(domain, model=Ticket):
            return model.search_count(domain)

        def selection(field, model=Ticket):
            return dict(model._fields[field]._description_selection(self.env))

        # Backlog: what is open now.
        open_dom = [('is_closed', '=', False)]
        unassigned = open_dom + [('user_id', '=', False)]
        overdue = open_dom + [('deadline', '<', today)]
        urgent = open_dom + [('priority', 'in', ('2', '3'))]
        blocked = open_dom + [('kanban_state', '=', 'blocked')]
        oldest = Ticket.search(open_dom, order='create_date asc', limit=1)

        age = []
        for key, label, low, high in (('0_1', _('Under 1 day'), 0, 0), ('1_3', _('1–3 days'), 1, 3),
                                      ('4_7', _('4–7 days'), 4, 7), ('7p', _('Over 7 days'), 8, None)):
            dom = open_dom + [('create_date', '<=', now - timedelta(days=low))]
            if high is not None:
                dom.append(('create_date', '>', now - timedelta(days=high + 1)))
            age.append({'key': key, 'label': label, 'count': count(dom), 'domain': dom})

        def grouped(domain, group, limit=None, label=None):
            rows = Ticket._read_group(domain + [(group, '!=', False)], [group], ['__count'],
                                      order='__count desc', limit=limit)
            return [{'name': label(v) if label else v.display_name, 'count': n,
                     'domain': domain + [(group, '=', v if label else v.id)]} for v, n in rows]

        priorities = selection('priority')
        by_stage = grouped(open_dom, 'stage_id')
        by_priority = sorted(grouped(open_dom, 'priority', label=priorities.get),
                             key=lambda r: -int(r['domain'][-1][2]))
        types = selection('ticket_type')
        by_type = grouped(open_dom, 'ticket_type', label=types.get)

        # The period: received (created) and closed.
        received = self._datetime_span(start, end, 'create_date')
        prev_received = self._datetime_span(pstart, pend, 'create_date')
        closed = [('is_closed', '=', True)] + self._datetime_span(start, end, 'close_date')
        prev_closed = [('is_closed', '=', True)] + self._datetime_span(pstart, pend, 'close_date')

        def avg_hours(domain):
            return Ticket._read_group(domain, [], ['resolution_hours:avg'])[0][0] or 0.0

        trend_unit, buckets = _trend_buckets(period, today)

        def by_day(domain, field):
            return {d.date(): n for d, n in Ticket._read_group(domain, [f'{field}:day'], ['__count'])}
        trend = [{'label': label, 'received': r, 'closed': c} for (label, _a, _b), r, c in zip(
            buckets, _sum_by_bucket(buckets, by_day(received, 'create_date')),
            _sum_by_bucket(buckets, by_day(closed, 'close_date')))]

        categories = grouped(received, 'category_id', limit=8)
        assets = grouped(received, 'asset_id', limit=6)

        # Agents: open now and closed in the period.
        open_by = dict(Ticket._read_group(open_dom + [('user_id', '!=', False)], ['user_id'], ['__count']))
        closed_by = dict(Ticket._read_group(closed + [('user_id', '!=', False)], ['user_id'], ['__count']))
        agents = sorted(({'name': u.display_name, 'open': open_by.get(u, 0), 'closed': closed_by.get(u, 0),
                          'domain': [('user_id', '=', u.id), '|', ('is_closed', '=', False), '&', '&'] + closed}
                         for u in set(open_by) | set(closed_by)),
                        key=lambda r: -(r['open'] + r['closed']))[:8]

        # Satisfaction on what was closed in the period.
        rated = closed + [('satisfaction', '!=', False)]
        ratings = selection('satisfaction')
        rated_counts = dict(Ticket._read_group(rated, ['satisfaction'], ['__count']))
        rated_total = sum(rated_counts.values())
        satisfaction = [{'key': k, 'name': label, 'count': rated_counts.get(k, 0),
                         'domain': closed + [('satisfaction', '=', k)]} for k, label in ratings.items()]

        # The asset register and consumable stock, at a glance.
        Asset = self.env['it.asset']
        asset_states = selection('state', Asset)
        asset_rows = [{'name': asset_states[s], 'count': count([('state', '=', s)], Asset),
                       'domain': [('state', '=', s)]} for s in ('available', 'assigned', 'maintenance', 'lost')]
        warranty = [('warranty_status', '=', 'expiring')]
        low_stock = [('is_low_stock', '=', True)]

        closed_count = count(closed)
        return {
            'period': {'start': str(start), 'end': str(end)},
            'open': {'count': count(open_dom), 'domain': open_dom,
                     'unassigned': count(unassigned), 'unassigned_domain': unassigned,
                     'oldest_days': (now - oldest.create_date).days if oldest else 0},
            'overdue': {'count': count(overdue), 'domain': overdue,
                        'blocked': count(blocked), 'blocked_domain': blocked},
            'urgent': {'count': count(urgent), 'domain': urgent},
            'received': {'count': count(received), 'domain': received, 'previous': count(prev_received)},
            'closed': {'count': closed_count, 'domain': closed, 'previous': count(prev_closed)},
            'resolution': {'hours': round(avg_hours(closed), 1), 'previous': round(avg_hours(prev_closed), 1)},
            'satisfaction': {'rows': satisfaction, 'rated': rated_total, 'domain': rated,
                             'great_pct': round(rated_counts.get('great', 0) * 100.0 / rated_total)
                             if rated_total else 0},
            'age': age, 'by_stage': by_stage, 'by_priority': by_priority, 'by_type': by_type,
            'trend': trend, 'trend_unit': trend_unit,
            'categories': categories, 'assets': assets, 'agents': agents,
            'register': {'rows': asset_rows,
                         'warranty': count(warranty, Asset), 'warranty_domain': warranty,
                         'low_stock': count(low_stock, self.env['it.consumable']), 'low_stock_domain': low_stock},
        }

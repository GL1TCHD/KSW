import re
from collections import defaultdict

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.fields import Command

# Old per-note lines are labelled "<note> · <YYYY-MM-DD>".
_NOTE_DAY = re.compile(r'(\d{4}-\d{2}-\d{2})\s*$')

# Trip sizes the fleet actually runs: 54,320 of 61,834 trailer notes in 2026
# are 32 m³, and 2,860 of 3,026 Isuzu notes are 6 m³.
_TRIP_UNITS = (('Trip (32 m³)', 32.0), ('Trip (6 m³)', 6.0))


def _reference_root(uom):
    while uom.relative_uom_id:
        uom = uom.relative_uom_id
    return uom


class UomUom(models.Model):
    _inherit = 'uom.uom'

    def _ksw_converts_to(self, other):
        """Same reference chain, so a quantity in one is meaningful in the other."""
        self.ensure_one()
        return bool(other) and _reference_root(self) == _reference_root(other)

    def _ksw_qty(self, qty, to_unit):
        """Exact conversion. Core's `_compute_quantity` rounds to the 'Product
        Unit' precision, which turns a 30 m³ load into 0.94 of a 32 m³ trip
        and makes the printed quantity disagree with the amount."""
        self.ensure_one()
        if not to_unit or to_unit == self:
            return qty
        return qty * self.factor / to_unit.factor

    def _ksw_price(self, price, to_unit):
        self.ensure_one()
        if not to_unit or to_unit == self:
            return price
        return price * to_unit.factor / self.factor

    @api.model
    def _ksw_ensure_trip_units(self):
        """A trip as a real unit of m³, so showing an invoice in trips is
        Odoo's own conversion. Re-run on every upgrade; creates only what is
        missing. Keyed on the active m³, because the one the BAS products use
        was created by the GL import and has no external id."""
        cubic = self.search([('name', '=', 'm³'), ('active', '=', True)], limit=1) \
            or self.env.ref('uom.product_uom_cubic_meter', raise_if_not_found=False)
        if not cubic:
            return
        for name, size in _TRIP_UNITS:
            if not self.with_context(active_test=False).search(
                    [('name', '=', name), ('relative_uom_id', '=', cubic.id)], limit=1):
                self.create({'name': name, 'relative_factor': size,
                             'relative_uom_id': cubic.id})


class AccountMoveLine(models.Model):
    _inherit = 'account.move.line'

    # The product sections the delivery-note invoice is built from, and the
    # unit each one is printed in. The note lines underneath always stay in
    # the product's own unit, so the money never goes through a conversion.
    x_water_section = fields.Boolean(copy=False)
    x_display_uom_id = fields.Many2one('uom.uom', string='Print In')
    # One line billing many delivery notes of one product, rate and revenue
    # account: the journal entry is client / sales / VAT, the notes are
    # reached through `sale_line_ids` (see sale_order_line_share.py).
    x_water_grouped = fields.Boolean(string='Delivery Notes Line')


class AccountMove(models.Model):
    _inherit = 'account.move'

    x_water_summary = fields.Boolean(
        string='Print One Line per Product', copy=False,
        help='The printed invoice shows one line per product. Untick to also '
             'print the list of delivery notes the invoice bills.',
    )
    x_has_water_sections = fields.Boolean(compute='_compute_has_water_sections')
    x_water_note_ids = fields.Many2many(
        'stock.picking', string='Delivery Notes', compute='_compute_water_note_ids',
        compute_sudo=True)
    x_water_note_count = fields.Integer(compute='_compute_water_note_ids')

    @api.depends('invoice_line_ids.x_water_section', 'invoice_line_ids.x_water_grouped')
    def _compute_has_water_sections(self):
        for move in self:
            move.x_has_water_sections = any(
                l.x_water_section or l.x_water_grouped for l in move.invoice_line_ids)

    @api.depends('invoice_line_ids.sale_line_ids')
    def _compute_water_note_ids(self):
        for move in self:
            notes = move.invoice_line_ids.sale_line_ids.order_id.picking_ids.filtered(
                'x_is_water_delivery')
            if 'x_bas_delivery_ids' in move._fields:
                notes |= move.x_bas_delivery_ids
            move.x_water_note_ids = notes
            move.x_water_note_count = len(notes)

    def action_view_water_notes(self):
        self.ensure_one()
        notes = self.x_water_note_ids
        action = {
            'type': 'ir.actions.act_window',
            'name': _('Delivery Notes'),
            'res_model': 'stock.picking',
            'domain': [('id', 'in', notes.ids)],
            'context': {'create': False},
        }
        if len(notes) == 1:
            action.update(view_mode='form', res_id=notes.id, views=[(self.env.ref(
                'KSW_water_delivery.view_water_delivery_note_form').id, 'form')])
        else:
            action.update(view_mode='list,form', views=[
                (self.env.ref('KSW_water_delivery.view_water_delivery_note_list').id, 'list'),
                (self.env.ref('KSW_water_delivery.view_water_delivery_note_form').id, 'form')])
        return action

    def _water_section_lines(self, section):
        return self.invoice_line_ids.filtered(
            lambda l: l.display_type == 'product' and l.parent_id == section)

    def _get_move_lines_to_report(self):
        lines = super()._get_move_lines_to_report()
        if not self.x_water_summary:
            return lines
        return lines.filtered(lambda l: not (
            l.x_water_section or l.x_water_grouped or l.parent_id.x_water_section))

    def _water_report_sa_columns(self):
        """Whether the Saudi invoice layout (VAT / excl. / incl. columns) is
        the one printing."""
        view = self.env.ref('l10n_sa.l10n_sa_report_invoice_document', raise_if_not_found=False)
        return bool(view and view.active)

    def _water_summary_rows(self):
        """One printed row per product line (or, on an invoice still in the
        old shape, per section of note lines), in its display unit.

        The amount is the line's own, never re-derived from the converted
        quantity, so the invoice total cannot move. The quantity is printed
        exactly (a 30 m³ load is 0.9375 of a 32 m³ trip), so that
        quantity x unit price still reads true to the halala.
        """
        self.ensure_one()
        rows = []
        for item in self.invoice_line_ids.filtered(
                lambda l: l.x_water_section or l.x_water_grouped).sorted('sequence'):
            if item.x_water_grouped:
                lines, unit = item, item.x_display_uom_id or item.product_uom_id
            else:
                lines = self._water_section_lines(item)
                if not lines:
                    continue
                unit = item.x_display_uom_id or lines[0].product_uom_id
            quantity = sum(l.product_uom_id._ksw_qty(l.quantity, unit) for l in lines)
            prices = {round(l.product_uom_id._ksw_price(l.price_unit, unit), 6) for l in lines}
            subtotal = sum(lines.mapped('price_subtotal'))
            price_unit = prices.pop() if len(prices) == 1 else (subtotal / quantity if quantity else 0.0)
            rows.append({
                'name': lines[0].product_id.display_name,
                'quantity': ('%.4f' % quantity).rstrip('0').rstrip('.'),
                'uom': unit.display_name,
                'price_unit': price_unit,
                'taxes': ', '.join(sorted({t.tax_label for t in lines.tax_ids if t.tax_label})),
                'price_subtotal': subtotal,
                'price_total': sum(lines.mapped('price_total')),
            })
        return rows

    def _water_note_rows(self):
        """The delivery notes a grouped invoice bills, for its printed list:
        one dict per note, its amount where an order line carries it."""
        self.ensure_one()
        rows = []
        for line in self.invoice_line_ids.filtered('x_water_grouped').sorted('sequence'):
            for sale_line in line.sudo().sale_line_ids:
                note = sale_line.order_id.picking_ids.filtered('x_is_water_delivery')[:1]
                rows.append({
                    'name': note.name or sale_line.order_id.name,
                    'date': note.x_delivery_date,
                    'product': sale_line.product_id.display_name,
                    'quantity': sale_line.qty_delivered or sale_line.product_uom_qty,
                    'uom': sale_line.product_uom_id.name,
                    'amount': sale_line.price_subtotal,
                })
        if not rows and 'x_bas_delivery_ids' in self._fields:
            for note in self.sudo().x_bas_delivery_ids:
                moves = note.move_ids
                rows.append({
                    'name': note.name,
                    'date': note.x_delivery_date or (note.date_done and note.date_done.date()),
                    'product': ', '.join(moves.product_id.mapped('display_name')),
                    'quantity': sum(moves.mapped('quantity')),
                    'uom': moves.product_uom[:1].name or '',
                    'amount': False,
                })
        return sorted(rows, key=lambda r: (r['date'] or fields.Date.today(), r['name']))

    # ------------------------------------------------------------------
    # One line per product, rate and revenue account
    # ------------------------------------------------------------------
    @api.model
    def _water_merge_note_rows(self, rows, display_uoms=None, sequence=0):
        """Invoice line values billing many delivery notes in a few lines.

        `rows` is one dict per note: product, uom, price (the rate), discount,
        taxes, account (False: the product's own), qty, amount (untaxed, as
        the note itself was priced), analytic (dict or False), sale_line_ids
        (ids), day, and optional `base` values to start the line from.

        Notes become one line when they share product, unit, rate, discount,
        taxes and revenue account -- a mid-month rate change or a second
        branch stays its own line. The line's unit price is the notes' total
        over their quantity, so the invoice total is the notes' to the halala;
        their cost centres become one split, weighted by amount.
        """
        display_uoms = display_uoms or {}
        groups = defaultdict(list)
        for row in rows:
            groups[(row['product'].id, row['uom'].id, round(row['price'], 4),
                    row.get('discount') or 0.0, tuple(sorted(row['taxes'].ids)),
                    row['account'].id if row.get('account') else False)].append(row)
        commands = []
        for key in sorted(groups, key=lambda k: (groups[k][0]['product'].display_name, k[2], k[5])):
            group = groups[key]
            first = group[0]
            product, uom = first['product'], first['uom']
            qty = sum(r['qty'] for r in group)
            amount = sum(r['amount'] for r in group)
            discount = first.get('discount') or 0.0
            gross = qty * (1 - discount / 100.0)
            days = [r['day'] for r in group if r.get('day')]
            sequence += 1
            vals = dict(first.get('base') or {})
            vals.update({
                'display_type': 'product',
                'sequence': sequence,
                'product_id': product.id,
                'product_uom_id': uom.id,
                'quantity': qty,
                'price_unit': amount / gross if gross else first['price'],
                'discount': discount,
                'tax_ids': [Command.set(first['taxes'].ids)],
                'name': self._water_line_label(product, len(group), days),
                'x_water_grouped': True,
                'x_display_uom_id': (display_uoms.get(product.id) or uom).id,
            })
            if first.get('account'):
                vals['account_id'] = first['account'].id
            sale_lines = [i for r in group for i in (r.get('sale_line_ids') or [])]
            if sale_lines:
                vals['sale_line_ids'] = [Command.set(sale_lines)]
            distribution = self._water_merge_distribution(group)
            if distribution:
                vals['analytic_distribution'] = distribution
            else:
                vals.pop('analytic_distribution', None)
            commands.append(Command.create(vals))
        return commands

    @api.model
    def _water_line_label(self, product, count, days):
        if not days:
            return _('%(product)s: %(count)s delivery notes', product=product.display_name, count=count)
        return _('%(product)s: %(count)s delivery notes, %(start)s to %(end)s',
                 product=product.display_name, count=count,
                 start=min(days), end=max(days))

    @api.model
    def _water_merge_distribution(self, group):
        """The notes' cost centres as one split, weighted by amount, to the
        precision Odoo keeps; the rounding goes to the largest share so the
        split covers exactly what the notes did."""
        total = sum(abs(r['amount']) for r in group)
        if not total or not any(r.get('analytic') for r in group):
            return False
        pct, covered = defaultdict(float), 0.0
        for r in group:
            weight = abs(r['amount']) / total
            for key, share in (r.get('analytic') or {}).items():
                pct[key] += weight * share
            covered += weight * sum((r.get('analytic') or {}).values())
        digits = self.env['decimal.precision'].precision_get('Percentage Analytic')
        result = {k: round(v, digits) for k, v in pct.items() if round(v, digits)}
        if result:
            largest = max(result, key=result.get)
            result[largest] = round(result[largest] + round(covered, digits) - sum(result.values()), digits)
        return result

    def _water_regroup_note_lines(self):
        """Rewrite an invoice from the one-line-per-note shape into the
        grouped one, without moving its total, VAT or payments.

        A posted invoice is reset to draft, rewritten, checked to the halala
        and posted again; the payments matched to it are matched again.
        Returns the invoices rewritten.
        """
        done = self.browse()
        for inv in self:
            sections = inv.invoice_line_ids.filtered('x_water_section')
            if not sections:
                continue
            old, rows = sections, []
            for section in sections:
                lines = inv._water_section_lines(section)
                old |= lines
                for line in lines:
                    notes = line.sale_line_ids.order_id.picking_ids.filtered('x_is_water_delivery')
                    match = _NOTE_DAY.search(line.name or '')
                    day = notes[:1].x_delivery_date or (match and fields.Date.to_date(match.group(1)))
                    rows.append({
                        'product': line.product_id, 'uom': line.product_uom_id,
                        'price': line.price_unit, 'discount': line.discount,
                        'taxes': line.tax_ids, 'account': line.account_id,
                        'qty': line.quantity, 'amount': line.price_subtotal,
                        'analytic': line.analytic_distribution or False,
                        'sale_line_ids': line.sale_line_ids.ids, 'day': day,
                        'display_uom': section.x_display_uom_id,
                    })
            display_uoms = {r['product'].id: r['display_uom'] for r in rows if r['display_uom']}
            start = min(old.mapped('sequence'))
            commands = [Command.delete(l.id) for l in old] + self._water_merge_note_rows(
                rows, display_uoms=display_uoms, sequence=start - 1)
            inv._water_rewrite_lines({'invoice_line_ids': commands, 'x_water_summary': True})
            done |= inv
        return done

    def _water_rewrite_lines(self, vals):
        """Write `vals` on this invoice, posted or not, refusing if the total
        or the VAT would move; a posted invoice is re-posted and its payments
        matched again."""
        self.ensure_one()
        total, tax, posted = self.amount_total, self.amount_tax, self.state == 'posted'
        partners = self.env['account.move.line']
        if posted:
            rec = self.line_ids.filtered(lambda l: l.account_id.account_type == 'asset_receivable')
            partners = (rec.matched_debit_ids.debit_move_id | rec.matched_credit_ids.credit_move_id) - rec
            self.button_draft()
        self.write(vals)
        if self.currency_id.compare_amounts(self.amount_total, total) or \
                self.currency_id.compare_amounts(self.amount_tax, tax):
            raise UserError(_('%(inv)s: grouping the delivery notes moved the total from '
                              '%(old)s to %(new)s.', inv=self.name, old=total, new=self.amount_total))
        if posted:
            self._post(soft=False)
            rec = self.line_ids.filtered(lambda l: l.account_id.account_type == 'asset_receivable')
            others = partners.filtered(lambda l: not l.reconciled)
            if others:
                (rec | others).reconcile()

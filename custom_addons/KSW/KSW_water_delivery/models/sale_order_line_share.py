from collections import defaultdict

from odoo import models
from odoo.tools import float_compare


class SaleOrderLine(models.Model):
    """A month-end invoice line can bill many delivery notes at once.

    Core counts every invoice line linked to a sale line in full
    (`_prepare_qty_invoiced`, `untaxed_amount_invoiced`, ...), which is right
    for one invoice line per order line and wrong for one "960 m³" line linked
    to thirty notes: each note would read 960 m³ invoiced. Here each linked
    note takes its own share of such a line, in proportion to what it
    delivered, so a note still reads exactly invoiced, a cancelled draft frees
    all of them, and a full credit note reopens all of them.

    Only lines linked to more than one order line take this path; every other
    line is left to core untouched.
    """
    _inherit = 'sale.order.line'

    def _water_share(self, invoice_line):
        """This order line's fraction of a line that bills several of them."""
        self.ensure_one()
        siblings = invoice_line.sale_line_ids
        if len(siblings) <= 1:
            return 1.0
        basis = {l: l.qty_delivered or l.product_uom_qty for l in siblings}
        total = sum(basis.values())
        return basis.get(self, 0.0) / total if total else 1.0 / len(siblings)

    def _water_share_qty(self, invoice_line):
        """The quantity of `invoice_line` that is this order line's, in this
        line's unit. When the invoice line bills the whole of every note --
        the normal case -- it is exactly what the note delivered, so no float
        residue can leave a fully billed note reading 'to invoice'."""
        qty = invoice_line.product_uom_id._compute_quantity(
            invoice_line.quantity, self.product_uom_id)
        siblings = invoice_line.sale_line_ids
        if len(siblings) <= 1:
            return qty
        delivered = sum(l.product_uom_id._compute_quantity(
            l.qty_delivered or l.product_uom_qty, self.product_uom_id) for l in siblings)
        if not float_compare(qty, delivered, precision_rounding=self.product_uom_id.rounding):
            return self.qty_delivered or self.product_uom_qty
        return qty * self._water_share(invoice_line)

    def _water_grouped(self):
        return self.filtered(lambda l: any(
            len(il.sale_line_ids) > 1 for il in l.invoice_lines))

    def _prepare_qty_invoiced(self):
        grouped = self._water_grouped()
        res = super(SaleOrderLine, self - grouped)._prepare_qty_invoiced() \
            if self - grouped else defaultdict(float)
        for line in grouped:
            for invoice_line in line._get_invoice_lines():
                move = invoice_line.move_id
                if move.state != 'cancel' or move.payment_state == 'invoicing_legacy':
                    qty = line._water_share_qty(invoice_line)
                    if move.move_type == 'out_invoice':
                        res[line] += qty
                    elif move.move_type == 'out_refund':
                        res[line] -= qty
        return res

    def _compute_qty_invoiced_posted(self):
        grouped = self._water_grouped()
        super(SaleOrderLine, self - grouped)._compute_qty_invoiced_posted()
        for line in grouped:
            posted = 0.0
            for invoice_line in line._get_invoice_lines():
                move = invoice_line.move_id
                if move.state == 'posted' or move.payment_state == 'invoicing_legacy':
                    posted += line._water_share_qty(invoice_line) * -move.direction_sign
            line.qty_invoiced_posted = posted

    def _compute_untaxed_amount_invoiced(self):
        grouped = self._water_grouped()
        super(SaleOrderLine, self - grouped)._compute_untaxed_amount_invoiced()
        for line in grouped:
            line.untaxed_amount_invoiced = line._water_share_amount('price_subtotal')

    def _compute_amount_invoiced(self):
        grouped = self._water_grouped()
        super(SaleOrderLine, self - grouped)._compute_amount_invoiced()
        for line in grouped:
            line.amount_invoiced = line._water_share_amount('price_total')

    def _water_share_amount(self, field):
        self.ensure_one()
        amount = 0.0
        for invoice_line in self._get_invoice_lines():
            move = invoice_line.move_id
            if move.state != 'posted' and move.payment_state != 'invoicing_legacy':
                continue
            value = invoice_line.currency_id._convert(
                invoice_line[field] * self._water_share(invoice_line), self.currency_id,
                self.company_id, move.invoice_date or move.date)
            if move.move_type == 'out_invoice':
                amount += value
            elif move.move_type == 'out_refund':
                amount -= value
        return amount

import pytz
from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import UserError

# Where the notes are delivered. Stored dates must not depend on who happened
# to trigger the compute, so this is the company's zone, not the user's.
_DEFAULT_TZ = 'Asia/Riyadh'


class StockPicking(models.Model):
    _inherit = 'stock.picking'

    # The day the water reached the client -- what month end bills by. For a
    # note captured offline that is the phone's capture time, not the day a
    # dispatcher later validated it; otherwise a delivery made on 31 August and
    # accepted on 2 September lands in September's invoice.
    x_delivery_date = fields.Date(
        string='Delivered On', compute='_compute_delivery_date',
        store=True, index=True,
    )
    # Stored mirrors of the billing state so the billing clerk can filter on
    # them: the role holds no `sale.order` access, and a search through
    # `sale_id.invoice_status` is a read of `sale.order`.
    x_invoice_status = fields.Selection(
        related='sale_id.invoice_status', string='Invoice Status', store=True,
    )
    x_invoice_id = fields.Many2one(
        'account.move', string='Invoice', compute='_compute_invoice_id',
        store=True, compute_sudo=True, index='btree_not_null', copy=False,
    )

    @api.depends('x_is_water_delivery', 'date_done', 'x_captured_at')
    def _compute_delivery_date(self):
        for picking in self:
            moment = picking.x_captured_at or picking.date_done
            if not (picking.x_is_water_delivery and moment):
                picking.x_delivery_date = False
                continue
            tz = pytz.timezone(picking.company_id.partner_id.tz or _DEFAULT_TZ)
            picking.x_delivery_date = pytz.utc.localize(moment).astimezone(tz).date()

    @api.depends('sale_id.order_line.invoice_lines.move_id.state')
    def _compute_invoice_id(self):
        for picking in self:
            moves = picking.sale_id.order_line.invoice_lines.move_id.filtered(
                lambda m: m.move_type == 'out_invoice' and m.state != 'cancel')
            picking.x_invoice_id = moves.sorted('id')[-1:] if moves else False

    # ------------------------------------------------------------------
    # Month end: a client's notes become one invoice
    # ------------------------------------------------------------------
    @api.model
    def _water_invoiceable_domain(self, date_from=None, date_to=None):
        """Delivered water notes still owing an invoice line.

        Delivered means validated: a note held for a dispatcher is not, and
        `invoice_policy='delivery'` would bill nothing for it anyway. A note
        whose branch requires a signature is only billed once it has one --
        the signature is what the client pays against.
        """
        domain = [
            ('x_is_water_delivery', '=', True),
            ('state', '=', 'done'),
            ('x_invoice_status', '=', 'to invoice'),
            '|', ('x_signature_required', '=', False), ('x_signed_on', '!=', False),
        ]
        if date_from:
            domain.append(('x_delivery_date', '>=', date_from))
        if date_to:
            domain.append(('x_delivery_date', '<=', date_to))
        return domain

    def _water_invoice_lines(self):
        """The sale order lines these notes still have to invoice."""
        return self.sale_id.order_line.filtered(
            lambda l: not l.display_type and l.qty_to_invoice > 0)

    def _water_group_key(self, line):
        return (line.product_id, line.price_unit, line.discount,
                line.tax_ids, line.product_uom_id)

    def _water_create_invoice(self, invoice_date=None, show_notes=False, display_uoms=None):
        """One draft customer invoice for these notes.

        The journal entry is three kinds of line: the client's receivable,
        one sales line per (product, rate, revenue account) and the VAT. The
        notes are not listed one by one; each sales line is linked to the
        order lines of every note it bills, so the notes are one click away
        (Delivery Notes on the invoice) and each still reads exactly
        invoiced -- see `sale.order.line._water_share`. Cancelling the draft
        frees the notes, a full credit note reopens them.

        By default the invoice PRINTS one row per product (`x_water_summary`);
        `show_notes` adds the list of notes. `display_uoms` ({product id:
        uom.uom}) is the unit each product is printed in -- m³ shown as trips
        -- and is only ever a printing choice: the lines stay in the
        product's unit, so no amount goes through a conversion.

        Authority is the caller's job; this runs as whoever calls it.
        """
        notes = self.filtered(lambda p: p.x_is_water_delivery)
        if not notes:
            raise UserError(_('Select at least one water delivery note to invoice.'))

        not_delivered = notes.filtered(lambda p: p.state != 'done')
        if not_delivered:
            raise UserError(_(
                'These notes have not been delivered (validated) yet, so there '
                'is nothing to invoice on them: %s',
                ', '.join(not_delivered.mapped('name'))))

        clients = notes.partner_id.commercial_partner_id
        if len(clients) != 1:
            raise UserError(_(
                'One invoice is for one client. The selected notes belong to: %s',
                ', '.join(clients.mapped('display_name'))))

        # Two clerks invoicing the same client at once must not both bill the
        # same notes. Locking the order lines makes the second one wait, and
        # when the first commits the second fails to serialise and is retried
        # against the fresh quantities -- by which time there is nothing left.
        candidate_lines = notes.sale_id.order_line
        if candidate_lines:
            self.env.cr.execute(
                'SELECT id FROM sale_order_line WHERE id IN %s FOR UPDATE',
                [tuple(candidate_lines.ids)])
            candidate_lines.invalidate_recordset(['qty_to_invoice', 'qty_invoiced'])

        lines = notes._water_invoice_lines()
        already = notes.filtered(lambda p: not (p.sale_id.order_line & lines))
        if already:
            raise UserError(_(
                'These notes are already on an invoice: %s',
                ', '.join(already.mapped('name'))))

        orders = lines.order_id
        keys = orders._get_invoice_grouping_keys()
        if len({tuple(o[k] for k in keys) for o in orders}) > 1:
            raise UserError(_(
                'These notes cannot share one invoice: their orders differ in '
                'invoice address, currency or fiscal position.'))

        note_of = {}
        for note in notes:
            for line in note.sale_id.order_line:
                note_of[line] = note

        display_uoms = display_uoms or {}
        rows = []
        for line in lines:
            product, uom = line.product_id, line.product_uom_id
            unit = display_uoms.get(product.id) or uom
            if not uom._ksw_converts_to(unit):
                raise UserError(_(
                    '%(product)s is sold in %(uom)s and cannot be shown in %(unit)s.',
                    product=product.display_name, uom=uom.name, unit=unit.name))
            # The order line's own invoice values carry the branch revenue
            # account and the note's cost centres (both hooked on
            # `_prepare_invoice_line`), so Odoo's own "Create Invoice" and
            # this builder agree on them.
            base = line._prepare_invoice_line()
            base.pop('sale_line_ids', None)
            qty = line.qty_to_invoice
            rows.append({
                'base': base,
                'product': product, 'uom': uom, 'price': line.price_unit,
                'discount': line.discount, 'taxes': line.tax_ids,
                'account': self.env['account.account'].browse(base.get('account_id')),
                'qty': qty,
                'amount': line.currency_id.round(
                    qty * line.price_unit * (1 - (line.discount or 0.0) / 100.0)),
                'analytic': base.get('analytic_distribution') or line.analytic_distribution,
                'sale_line_ids': line.ids,
                'day': note_of[line].x_delivery_date,
            })
        invoice_lines = self.env['account.move']._water_merge_note_rows(
            rows, display_uoms=display_uoms)

        dates = notes.mapped('x_delivery_date')
        vals = orders[0]._prepare_invoice()
        vals.update({
            'invoice_line_ids': invoice_lines,
            # One order per trip, so the core origin would be hundreds of
            # order numbers. The notes are behind the lines; the header says
            # what the pile was.
            'invoice_origin': _('%(count)s delivery notes, %(start)s to %(end)s',
                                count=len(notes), start=min(dates), end=max(dates)),
            'ref': False,
            'delivery_date': max(dates),
            'x_water_summary': not show_notes,
        })
        if invoice_date:
            vals['invoice_date'] = invoice_date
        invoice = self.env['account.move'].with_context(
            default_move_type='out_invoice').create(vals)
        invoice.message_post(body=Markup(
            'Created from <b>%(count)s</b> water delivery notes delivered '
            '%(start)s to %(end)s.'
        ) % {'count': len(notes), 'start': min(dates), 'end': max(dates)})
        return invoice

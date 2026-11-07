"""What a migrated record was in BAS, in BAS's own words.

ERPs keep the references apart: our document number, the other party's number
(SAP XBLNR, Odoo's Bill Reference), the source document, and -- after a
migration -- the legacy document. BAS names its documents by type and number
(its archive files are «شراء اجل_6295.pdf» under branch 115), so the legacy
reference reads «شراء اجل 115/6295». It is derived from `x_bas_key`, never
typed, and lives in its own field so `ref` can carry the supplier's number.
"""
from odoo import api, fields, models


class KswBasDocumentType(models.Model):
    _name = 'ksw.bas.document.type'
    _description = 'BAS Document Type'
    _order = 'ftype, ftype2'
    _rec_name = 'name'

    ftype = fields.Char(string='FTYPE', required=True)
    ftype2 = fields.Integer(string='FTYPE2', default=0, required=True)
    name = fields.Char(string='BAS Name', required=True,
                       help="BAS9's own name for the document type (VOU_SETTING.NAME1).")

    _ftype_uniq = models.Constraint('unique(ftype, ftype2)', 'This BAS document type exists already.')

    @api.model_create_multi
    def create(self, vals_list):
        types = super().create(vals_list)
        self._relabel_documents()
        return types

    def write(self, vals):
        res = super().write(vals)
        self._relabel_documents()
        return res

    @api.model
    def _relabel_documents(self):
        """Recompute every stored label at once, in SQL: a stored compute only
        reruns when x_bas_key changes, not when a type is (re)named, and on
        install it runs before this module's data -- names not loaded yet."""
        self.env.flush_all()
        base = "split_part(x_bas_key, '#', 1)"
        for table in ('account_move', 'stock_picking'):
            self.env.cr.execute(f"""
                UPDATE {table} r SET x_bas_document = t.name || ' ' || split_part({base}, '/', 3)
                                                     || '/' || split_part({base}, '/', 4)
                  FROM ksw_bas_document_type t
                 WHERE r.x_bas_key ~ '^[0-9]{{3}}/[0-9]+/[^/]+/[^/#]+'
                   AND t.ftype = split_part(r.x_bas_key, '/', 1)
                   AND t.ftype2 = split_part(r.x_bas_key, '/', 2)::int
                   AND r.x_bas_document IS DISTINCT FROM
                       t.name || ' ' || split_part({base}, '/', 3) || '/' || split_part({base}, '/', 4)""")
        for model in ('account.move', 'stock.picking'):
            self.env[model].invalidate_model(['x_bas_document'])

    @api.model
    def _names(self):
        return {(t.ftype, t.ftype2): t.name for t in self.sudo().search([])}

    @api.model
    def _label(self, key, names=None):
        """«شراء اجل 115/6295» for '006/0/115/6295' (and its '#…' parts)."""
        if not key:
            return False
        base = key.split('#')[0]
        if base.startswith('OPENING/'):
            return 'رصيد افتتاحي %s' % base.split('/')[1]
        parts = base.split('/')
        if len(parts) != 4:
            return base
        ftype, ftype2, code2, number = parts
        names = names if names is not None else self._names()
        name = names.get((ftype, int(ftype2) if ftype2.isdigit() else 0)) or names.get((ftype, 0)) or ftype
        return '%s %s/%s' % (name, code2, number)


class KswBasDocumentMixin(models.AbstractModel):
    _name = 'ksw.bas.document.mixin'
    _description = 'BAS Document'

    x_bas_document = fields.Char(
        string='BAS Document', compute='_compute_x_bas_document', store=True, index='trigram',
        help='The BAS9 document this record was created from, as BAS names it: '
             'type, branch / number.')

    @api.depends('x_bas_key')
    def _compute_x_bas_document(self):
        Type = self.env['ksw.bas.document.type']
        names = Type._names()
        for rec in self:
            rec.x_bas_document = Type._label(rec.x_bas_key, names)


class AccountMove(models.Model):
    _name = 'account.move'
    _inherit = ['account.move', 'ksw.bas.document.mixin']


class StockPicking(models.Model):
    _name = 'stock.picking'
    _inherit = ['stock.picking', 'ksw.bas.document.mixin']


class AccountPayment(models.Model):
    _inherit = 'account.payment'

    x_bas_document = fields.Char(related='move_id.x_bas_document', string='BAS Document')

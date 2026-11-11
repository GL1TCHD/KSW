import base64
import hashlib
import io
import os

from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


# Fields only the server may set: the signing outcome and the hashes that
# prove it. Written solely through _write_internal(), which RPC cannot reach
# (a context flag would not do: any caller can pass any context key).
_SERVER_MANAGED_FIELDS = frozenset({
    'signed_pdf', 'signed_pdf_filename', 'signed_uid', 'signed_date',
    'source_pdf_sha256', 'signed_pdf_sha256', 'state',
})

# Everything that defines what was signed, by whom and where. Frozen once the
# document leaves draft; only the logged reset (_reset_to_draft) reopens it.
_LOCKED_WHEN_SIGNED = _SERVER_MANAGED_FIELDS | {
    'name', 'employee_id', 'signer_name', 'signature_reason',
    'source_pdf', 'source_pdf_filename',
    'signature_image', 'signature_sigtext', 'capture_method',
    'page_number', 'pos_x', 'pos_y', 'sig_width', 'sig_height', 'company_id',
}


def _sha256_of_b64(value):
    if not value:
        return False
    return hashlib.sha256(base64.b64decode(value)).hexdigest()


class KswSignatureDocument(models.Model):
    _name = 'ksw.signature.document'
    _description = 'Wacom Signature Document'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'create_date desc'

    name = fields.Char(required=True, tracking=True)
    employee_id = fields.Many2one('hr.employee', string='Employee', tracking=True)
    signer_name = fields.Char(string='Signer Name', default=lambda self: self.env.user.name)
    signature_reason = fields.Char(string='Reason', default='Document Approval')
    state = fields.Selection(
        [('draft', 'Draft'), ('signed', 'Signed'), ('cancelled', 'Cancelled')],
        default='draft', required=True, tracking=True,
    )

    source_pdf = fields.Binary(string='Source PDF', required=True, attachment=True)
    source_pdf_filename = fields.Char(string='Source Filename')
    source_pdf_sha256 = fields.Char(
        string='Source PDF SHA-256', readonly=True, copy=False,
        help='Fingerprint of the uploaded PDF. Also bound into the Wacom biometric data.')

    signature_image = fields.Binary(string='Captured Signature', attachment=True)
    # Wacom SigText: the pad's biometric record of the signature (pen
    # position, pressure and timing), bound to the signer name and the
    # source PDF's SHA-256. Kept so a handwriting examiner can verify it with
    # Wacom's tools; the PNG alone cannot be verified. Only the pad fills it.
    signature_sigtext = fields.Text(string='Biometric Signature Data', copy=False)
    has_biometric_data = fields.Boolean(
        string='Biometric Data Captured', compute='_compute_has_biometric_data')
    capture_method = fields.Selection(
        [('wacom_pad', 'Wacom Signature Pad'), ('manual', 'Mouse / Touch')],
        string='Captured Via', readonly=True,
    )

    page_number = fields.Integer(
        string='Page', default=1,
        help='1-based page number of the source PDF to stamp the signature on.',
    )
    pos_x = fields.Float(string='X (pt from left)', default=350.0)
    pos_y = fields.Float(string='Y (pt from bottom)', default=50.0)
    sig_width = fields.Float(string='Width (pt)', default=180.0)
    sig_height = fields.Float(string='Height (pt)', default=60.0)

    signed_pdf = fields.Binary(string='Signed PDF', readonly=True, attachment=True, copy=False)
    signed_pdf_filename = fields.Char(string='Signed Filename', readonly=True, copy=False)
    signed_uid = fields.Many2one('res.users', string='Signed By', readonly=True, copy=False)
    signed_date = fields.Datetime(string='Signed On', readonly=True, copy=False)
    signed_pdf_sha256 = fields.Char(
        string='Signed PDF SHA-256', readonly=True, copy=False,
        help='Fingerprint of the signed PDF at the moment of signing. Any later '
             'change to the file, even one byte, gives a different value.')

    company_id = fields.Many2one('res.company', default=lambda self: self.env.company)

    @api.depends('signature_sigtext')
    def _compute_has_biometric_data(self):
        for rec in self:
            rec.has_biometric_data = bool(rec.signature_sigtext)

    @api.onchange('source_pdf')
    def _onchange_source_pdf_sha256(self):
        # Shown before the first save so a pad capture on a new record can
        # already bind the PDF's fingerprint. Display only: the field is
        # readonly, so the client never sends it back; create()/write()
        # recompute it from the PDF itself.
        for rec in self:
            rec.source_pdf_sha256 = _sha256_of_b64(rec.source_pdf)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('state', 'draft') == 'draft':
                vals.pop('state', None)  # the client sends the default
            if _SERVER_MANAGED_FIELDS & set(vals):
                raise UserError(_('Signing results and fingerprints are set by the system only.'))
            if 'source_pdf' in vals:
                vals['source_pdf_sha256'] = _sha256_of_b64(vals['source_pdf'])
        return super().create(vals_list)

    def write(self, vals):
        if _SERVER_MANAGED_FIELDS & set(vals):
            raise UserError(_('Signing results and fingerprints are set by the system only.'))
        if _LOCKED_WHEN_SIGNED & set(vals) and any(r.state != 'draft' for r in self):
            raise UserError(_(
                'A signed document is locked. A Signature Manager can reset it '
                'to draft (with a reason, kept in the history) if it must change.'))
        vals = dict(vals)
        if 'source_pdf' in vals:
            vals['source_pdf_sha256'] = _sha256_of_b64(vals['source_pdf'])
        # A signature replaced by any other means (draw, upload, clear) no
        # longer matches the pad's biometric record: drop it.
        if 'signature_image' in vals and 'signature_sigtext' not in vals:
            vals['signature_sigtext'] = False
        return super().write(vals)

    def _write_internal(self, vals):
        """Write the signing outcome, bypassing the guards in write()."""
        return super().write(vals)

    @api.ondelete(at_uninstall=False)
    def _unlink_except_signed(self):
        if any(rec.state == 'signed' for rec in self):
            raise UserError(_('A signed document cannot be deleted.'))

    @api.constrains('page_number')
    def _check_page_number(self):
        for rec in self:
            if rec.page_number < 1:
                raise ValidationError(_('Page number must be at least 1.'))

    @api.constrains('sig_width', 'sig_height')
    def _check_sig_size(self):
        for rec in self:
            if rec.sig_width <= 0 or rec.sig_height <= 0:
                raise ValidationError(_('Signature width/height must be positive.'))

    def action_apply_signature(self):
        for rec in self:
            if rec.state != 'draft':
                raise UserError(_('Only draft documents can be signed.'))
            if not rec.source_pdf:
                raise UserError(_('Upload a source PDF first.'))
            if not rec.signature_image:
                raise UserError(_('Capture a signature first (Wacom pad or draw with mouse/touch).'))

            pdf_bytes = rec._stamp_signature_on_pdf()
            base_name = os.path.splitext(rec.source_pdf_filename or rec.name or 'document')[0]
            signed_hash = hashlib.sha256(pdf_bytes).hexdigest()
            rec._write_internal({
                'signed_pdf': base64.b64encode(pdf_bytes),
                'signed_pdf_filename': f'{base_name}_signed.pdf',
                'signed_pdf_sha256': signed_hash,
                'state': 'signed',
                'signed_uid': self.env.uid,
                'signed_date': fields.Datetime.now(),
            })

            method_label = dict(rec._fields['capture_method'].selection).get(
                rec.capture_method, _('Unknown'))
            body = Markup(
                '<strong>✅ Document Signed</strong><br/>'
                '<b>By:</b> %(user)s<br/>'
                '<b>Capture method:</b> %(method)s<br/>'
                '<b>Stamped on page:</b> %(page)s<br/>'
                '<b>Biometric data:</b> %(bio)s<br/>'
                '<b>Source PDF SHA-256:</b> <code>%(src)s</code><br/>'
                '<b>Signed PDF SHA-256:</b> <code>%(signed)s</code>'
            ) % {
                'user': self.env.user.name,
                'method': method_label,
                'page': rec.page_number,
                'bio': _('Captured') if rec.signature_sigtext else _('Not captured'),
                'src': rec.source_pdf_sha256 or '-',
                'signed': signed_hash,
            }
            rec.message_post(body=body, subtype_xmlid='mail.mt_note')
        return True

    def _check_signature_manager(self):
        if not self.env.su and not self.env.user.has_group(
                'KSW_wacom_signature.group_wacom_signature_manager'):
            raise UserError(_('Only a Wacom Signature Manager can reset a signed document back to draft.'))

    def action_reset_draft(self):
        self._check_signature_manager()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Reset Signed Document'),
            'res_model': 'ksw.signature.reset.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_document_id': self.ensure_one().id},
        }

    def _reset_to_draft(self, reason):
        """Reopen a signed document. The signed PDF is kept as a chatter
        attachment together with its fingerprint and the reason, so the reset
        never erases what was signed."""
        self._check_signature_manager()
        if not (reason or '').strip():
            raise UserError(_('A reason is required to reset a signed document.'))
        for rec in self:
            if rec.state != 'signed':
                raise UserError(_('Only signed documents can be reset.'))
            attachments = []
            if rec.signed_pdf:
                attachments.append((rec.signed_pdf_filename or 'signed.pdf',
                                    base64.b64decode(rec.signed_pdf)))
            body = Markup(
                '<strong>↩️ Signed Document Reset to Draft</strong><br/>'
                '<b>By:</b> %(user)s<br/>'
                '<b>Reason:</b> %(reason)s<br/>'
                '<b>Originally signed by:</b> %(signer)s on %(date)s<br/>'
                '<b>Signed PDF SHA-256:</b> <code>%(hash)s</code><br/>'
                'The signed PDF is attached to this message.'
            ) % {
                'user': self.env.user.name,
                'reason': reason.strip(),
                'signer': rec.signed_uid.name or '-',
                'date': rec.signed_date or '-',
                'hash': rec.signed_pdf_sha256 or '-',
            }
            rec.sudo().message_post(body=body, attachments=attachments,
                                    subtype_xmlid='mail.mt_note')
            rec._write_internal({
                'state': 'draft',
                'signed_pdf': False,
                'signed_pdf_filename': False,
                'signed_pdf_sha256': False,
                'signed_uid': False,
                'signed_date': False,
            })
        return True

    def action_verify_integrity(self):
        """Check the stored signed PDF still matches the fingerprint taken
        at signing."""
        self.ensure_one()
        if self.state != 'signed' or not self.signed_pdf:
            raise UserError(_('This document has not been signed yet.'))
        ok = _sha256_of_b64(self.signed_pdf) == self.signed_pdf_sha256
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'type': 'success' if ok else 'danger',
                'sticky': not ok,
                'title': _('Integrity Check'),
                'message': _('The signed PDF is unchanged since it was signed.') if ok else
                _('WARNING: the stored signed PDF does not match the fingerprint taken at signing.'),
            },
        }

    def _stamp_signature_on_pdf(self):
        """Overlay the captured signature PNG onto one page of the source PDF.

        Reuses the reader/writer pair and reportlab overlay technique from
        odoo.tools.pdf.add_banner, but merges the image onto a single chosen
        page instead of every page.
        """
        self.ensure_one()
        from odoo.tools.pdf import PdfFileReader, PdfFileWriter  # noqa: PLC0415
        from reportlab.lib.utils import ImageReader  # noqa: PLC0415
        from reportlab.pdfgen import canvas  # noqa: PLC0415

        reader = PdfFileReader(
            io.BytesIO(base64.b64decode(self.source_pdf)), strict=False, overwriteWarnings=False)
        num_pages = reader.getNumPages()
        if num_pages < 1:
            raise UserError(_('The source PDF has no pages.'))
        target_index = min(max(self.page_number, 1), num_pages) - 1

        sig_image = ImageReader(io.BytesIO(base64.b64decode(self.signature_image)))

        writer = PdfFileWriter()
        for index in range(num_pages):
            page = reader.getPage(index)
            if index == target_index:
                width = float(abs(page.mediaBox.getWidth()))
                height = float(abs(page.mediaBox.getHeight()))

                packet = io.BytesIO()
                can = canvas.Canvas(packet, pagesize=(width, height))
                can.drawImage(
                    sig_image,
                    self.pos_x, self.pos_y,
                    width=self.sig_width, height=self.sig_height,
                    mask='auto', preserveAspectRatio=True,
                )
                can.save()
                packet.seek(0)

                overlay_page = PdfFileReader(packet, overwriteWarnings=False).getPage(0)
                if '/Annots' in page:
                    del page['/Annots']
                page.mergePage(overlay_page)
            writer.addPage(page)

        output = io.BytesIO()
        writer.write(output)
        return output.getvalue()

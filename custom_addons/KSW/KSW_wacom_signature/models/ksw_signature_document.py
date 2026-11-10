import base64
import io
import os

from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


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

    signature_image = fields.Binary(string='Captured Signature', attachment=True)
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

    company_id = fields.Many2one('res.company', default=lambda self: self.env.company)

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
            rec.write({
                'signed_pdf': base64.b64encode(pdf_bytes),
                'signed_pdf_filename': f'{base_name}_signed.pdf',
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
                '<b>Stamped on page:</b> %(page)s'
            ) % {
                'user': self.env.user.name,
                'method': method_label,
                'page': rec.page_number,
            }
            rec.message_post(body=body, subtype_xmlid='mail.mt_note')
        return True

    def action_reset_draft(self):
        if not self.env.user.has_group('KSW_wacom_signature.group_wacom_signature_manager'):
            raise UserError(_('Only a Wacom Signature Manager can reset a signed document back to draft.'))
        for rec in self:
            rec.write({
                'state': 'draft',
                'signed_pdf': False,
                'signed_pdf_filename': False,
                'signed_uid': False,
                'signed_date': False,
            })
        return True

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

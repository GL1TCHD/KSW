import base64
import hashlib

from markupsafe import Markup

from odoo import _, api, fields, models


class KswSignatureVerifyWizard(models.TransientModel):
    """Answer "is this PDF one we signed, unchanged?" for a file someone
    hands over. Matches on the SHA-256 taken at signing, across every signed
    document (sudo), but reveals only who signed what and when."""
    _name = 'ksw.signature.verify.wizard'
    _description = 'Verify a Signed PDF'

    pdf_file = fields.Binary(string='PDF to Check', required=True)
    pdf_filename = fields.Char()
    file_sha256 = fields.Char(string='File SHA-256', compute='_compute_result')
    matched = fields.Boolean(compute='_compute_result')
    result = fields.Html(compute='_compute_result')

    @api.depends('pdf_file')
    def _compute_result(self):
        Document = self.env['ksw.signature.document'].sudo()
        for wiz in self:
            if not wiz.pdf_file:
                wiz.file_sha256 = wiz.result = False
                wiz.matched = False
                continue
            digest = hashlib.sha256(base64.b64decode(wiz.pdf_file)).hexdigest()
            doc = Document.search([('signed_pdf_sha256', '=', digest)], limit=1)
            wiz.file_sha256 = digest
            wiz.matched = bool(doc)
            if doc:
                wiz.result = Markup(
                    '<p class="text-success"><b>✅ %(verdict)s</b></p>'
                    '<p>%(doc_l)s <b>%(name)s</b><br/>%(signer_l)s %(signer)s<br/>'
                    '%(date_l)s %(date)s (%(user)s)</p>'
                ) % {
                    'verdict': _('Genuine and unchanged.'),
                    'doc_l': _('Document:'), 'name': doc.name,
                    'signer_l': _('Signer:'), 'signer': doc.signer_name or '-',
                    'date_l': _('Signed on:'), 'date': doc.signed_date or '-',
                    'user': doc.signed_uid.name or '-',
                }
            else:
                wiz.result = Markup(
                    '<p class="text-danger"><b>❌ %(verdict)s</b></p><p>%(detail)s</p>'
                ) % {
                    'verdict': _('No match.'),
                    'detail': _('This file is not a document signed in Odoo, or it '
                                'has been changed since it was signed.'),
                }

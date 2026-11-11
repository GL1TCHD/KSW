import base64
import hashlib
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    """Fingerprint documents created before 19.0.1.1.0. A signed document's
    hash is taken of the file as stored today, not at signing, so the
    chatter says so."""
    from odoo import SUPERUSER_ID, api  # noqa: PLC0415
    from markupsafe import Markup  # noqa: PLC0415

    env = api.Environment(cr, SUPERUSER_ID, {})
    docs = env['ksw.signature.document'].with_context(active_test=False).search([])
    for doc in docs:
        vals = {}
        if doc.source_pdf and not doc.source_pdf_sha256:
            vals['source_pdf_sha256'] = hashlib.sha256(base64.b64decode(doc.source_pdf)).hexdigest()
        if doc.signed_pdf and not doc.signed_pdf_sha256:
            vals['signed_pdf_sha256'] = hashlib.sha256(base64.b64decode(doc.signed_pdf)).hexdigest()
            doc.message_post(body=Markup(
                'Signed PDF fingerprint recorded on upgrade (taken from the file as '
                'stored now, not at the moment of signing): <code>%s</code>'
            ) % vals['signed_pdf_sha256'], subtype_xmlid='mail.mt_note')
        if vals:
            doc._write_internal(vals)
    _logger.info('KSW_wacom_signature: fingerprinted %s document(s).', len(docs))

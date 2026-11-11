import base64
import hashlib
import io

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, new_test_user, tagged

# 1x1 transparent PNG
PNG = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII='


def _pdf_b64():
    from reportlab.pdfgen import canvas  # noqa: PLC0415
    buf = io.BytesIO()
    can = canvas.Canvas(buf, pagesize=(595, 842))
    can.drawString(72, 760, 'Handover form')
    can.save()
    return base64.b64encode(buf.getvalue())


@tagged('post_install', '-at_install')
class TestSignatureIntegrity(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = new_test_user(cls.env, 'sig_user', groups='base.group_user,KSW_wacom_signature.group_wacom_signature_user')
        cls.manager = new_test_user(cls.env, 'sig_mgr', groups='base.group_user,KSW_wacom_signature.group_wacom_signature_manager')
        cls.Doc = cls.env['ksw.signature.document']

    def _doc(self, **vals):
        return self.Doc.with_user(self.user).create({
            'name': 'Handover', 'source_pdf': _pdf_b64(), 'source_pdf_filename': 'h.pdf', **vals,
        })

    def _signed(self):
        doc = self._doc(signature_image=PNG, signature_sigtext='BIO', capture_method='wacom_pad')
        doc.action_apply_signature()
        return doc

    def test_source_hash_on_create_and_replace(self):
        doc = self._doc()
        self.assertEqual(doc.source_pdf_sha256,
                         hashlib.sha256(base64.b64decode(doc.source_pdf)).hexdigest())
        old = doc.source_pdf_sha256
        doc.write({'source_pdf': base64.b64encode(b'%PDF-1.4 other')})
        self.assertNotEqual(doc.source_pdf_sha256, old)

    def test_signing_records_signed_hash_and_keeps_biometric(self):
        doc = self._signed()
        self.assertEqual(doc.state, 'signed')
        self.assertEqual(doc.signed_pdf_sha256,
                         hashlib.sha256(base64.b64decode(doc.signed_pdf)).hexdigest())
        self.assertTrue(doc.has_biometric_data)
        self.assertEqual(doc.action_verify_integrity()['params']['type'], 'success')

    def test_server_managed_fields_refused(self):
        with self.assertRaises(UserError):
            self._doc(signed_pdf_sha256='forged')
        doc = self._doc()
        with self.assertRaises(UserError):
            doc.write({'source_pdf_sha256': 'forged'})
        with self.assertRaises(UserError):
            doc.write({'state': 'signed'})

    def test_replacing_signature_drops_biometric(self):
        doc = self._doc(signature_image=PNG, signature_sigtext='BIO', capture_method='wacom_pad')
        doc.write({'signature_image': PNG})
        self.assertFalse(doc.signature_sigtext)

    def test_signed_document_locked(self):
        doc = self._signed()
        for vals in ({'source_pdf': _pdf_b64()}, {'signature_image': PNG},
                     {'pos_x': 10.0}, {'state': 'draft'}, {'signer_name': 'Someone'}):
            with self.assertRaises(UserError):
                doc.write(vals)
        with self.assertRaises(UserError):
            doc.unlink()

    def test_reset_needs_manager_and_reason_and_keeps_signed_copy(self):
        doc = self._signed()
        signed_hash = doc.signed_pdf_sha256
        with self.assertRaises(UserError):
            doc.action_reset_draft()
        with self.assertRaises(UserError):
            doc.with_user(self.manager)._reset_to_draft('  ')
        wizard = self.env['ksw.signature.reset.wizard'].with_user(self.manager).create({
            'document_id': doc.id, 'reason': 'Wrong page',
        })
        wizard.action_confirm()
        self.assertEqual(doc.state, 'draft')
        self.assertFalse(doc.signed_pdf_sha256)
        msg = doc.message_ids.filtered(lambda m: 'Wrong page' in (m.body or ''))
        self.assertEqual(len(msg), 1)
        self.assertIn(signed_hash, msg.body)
        self.assertEqual(len(msg.attachment_ids), 1)

    def test_verify_wizard(self):
        doc = self._signed()
        Wizard = self.env['ksw.signature.verify.wizard'].with_user(self.user)
        self.assertTrue(Wizard.new({'pdf_file': doc.signed_pdf}).matched)
        tampered = base64.b64encode(base64.b64decode(doc.signed_pdf) + b'\n')
        self.assertFalse(Wizard.new({'pdf_file': tampered}).matched)

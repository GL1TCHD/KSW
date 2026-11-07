from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestBasDocument(TransactionCase):

    def test_label_is_bas_type_branch_number(self):
        Type = self.env['ksw.bas.document.type']
        self.assertEqual(Type._label('006/0/115/6295'), 'شراء اجل 115/6295')
        self.assertEqual(Type._label('600/2/172/9184837#1'), 'اذون تسليم نقدي 172/9184837')
        self.assertEqual(Type._label('002/3/110/21137#120301063#rest'), 'بيع اجل لاذونات تسليم 110/21137')
        self.assertEqual(Type._label('018/0/010/26001047#32'), 'سند قيد 010/26001047')
        self.assertEqual(Type._label('OPENING/2026'), 'رصيد افتتاحي 2026')
        self.assertFalse(Type._label(False))

    def test_a_move_shows_its_bas_document(self):
        journal = self.env['account.journal'].search([('type', '=', 'general')], limit=1)
        move = self.env['account.move'].create({'journal_id': journal.id, 'x_bas_key': '101/0/999/990001'})
        self.assertEqual(move.x_bas_document, 'فاتورة صرف 999/990001')
        self.assertTrue(self.env['account.move'].search([('x_bas_document', 'ilike', 'فاتورة صرف 999/990001')]))

    def test_renaming_a_type_relabels_its_records(self):
        journal = self.env['account.journal'].search([('type', '=', 'general')], limit=1)
        move = self.env['account.move'].create({'journal_id': journal.id, 'x_bas_key': '018/0/999/990002#5'})
        self.env.ref('KSW_bas_gl_import.bas_doc_018_0').name = 'قيد يومية'
        self.assertEqual(move.x_bas_document, 'قيد يومية 999/990002')

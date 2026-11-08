from odoo.exceptions import UserError
from odoo.fields import Command
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestPurchaseRequestStep(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.supplier = cls.env['res.partner'].create({'name': 'PR Step Vendor', 'supplier_rank': 1})
        cls.product = cls.env['product.product'].create({
            'name': 'PR Step Item', 'is_storable': True, 'purchase_method': 'receive'})
        cls.picking_type = cls.env['stock.picking.type'].search(
            [('code', '=', 'incoming'), ('company_id', '=', cls.company.id)], limit=1)

    def _direct_rfq(self):
        return self.env['purchase.order'].create({
            'partner_id': self.supplier.id,
            'order_line': [Command.create({'product_id': self.product.id, 'product_qty': 2,
                                           'price_unit': 10})],
        })

    def _rfq_from_request(self):
        request = self.env['purchase.request'].create({
            'picking_type_id': self.picking_type.id,
            'line_ids': [Command.create({'product_id': self.product.id, 'product_qty': 2,
                                         'product_uom_id': self.product.uom_id.id})],
        })
        request.button_to_approve()
        request.button_approved()
        wizard = self.env['purchase.request.line.make.purchase.order'].with_context(
            active_model='purchase.request.line', active_ids=request.line_ids.ids,
            active_id=request.line_ids.id,
        ).create({'supplier_id': self.supplier.id})
        wizard.make_purchase_order()
        return request.line_ids.purchase_lines.order_id

    def _blacklisted(self):
        menu = self.env.ref('purchase_request.parent_menu_purchase_request')
        return menu.id in self.env['ir.ui.menu']._load_menus_blacklist()

    def test_step_off_confirms_directly_and_hides_menus(self):
        self.company.x_purchase_request_required = False
        order = self._direct_rfq()
        order.button_confirm()
        self.assertEqual(order.state, 'purchase')
        self.assertTrue(self._blacklisted())

    def test_step_on_refuses_an_rfq_without_request(self):
        self.company.x_purchase_request_required = True
        order = self._direct_rfq()
        with self.assertRaises(UserError):
            order.button_confirm()
        self.assertEqual(order.state, 'draft')
        self.assertFalse(self._blacklisted())

    def test_step_on_confirms_an_rfq_from_an_approved_request(self):
        self.company.x_purchase_request_required = True
        order = self._rfq_from_request()
        self.assertTrue(order)
        order.button_confirm()
        self.assertEqual(order.state, 'purchase')

    def test_step_on_refuses_a_line_added_outside_the_request(self):
        self.company.x_purchase_request_required = True
        order = self._rfq_from_request()
        extra = self.env['product.product'].create({'name': 'Not Requested', 'is_storable': True})
        order.write({'order_line': [Command.create({'product_id': extra.id, 'product_qty': 1,
                                                    'price_unit': 5})]})
        with self.assertRaises(UserError):
            order.button_confirm()

    def test_settings_checkbox_writes_the_company(self):
        settings = self.env['res.config.settings'].create({'x_purchase_request_required': True})
        settings.execute()
        self.assertTrue(self.company.x_purchase_request_required)

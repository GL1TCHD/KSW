from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestAssetAssignment(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        category = cls.env['it.asset.category'].search([], limit=1) or \
            cls.env['it.asset.category'].create({'name': 'Laptop'})
        cls.asset = cls.env['it.asset'].create({'name': 'Test Laptop', 'category_id': category.id})
        cls.employee = cls.env['hr.employee'].create({'name': 'Asset Holder'})

    def _assign(self, **vals):
        return self.env['it.asset.assignment'].create({
            'asset_id': self.asset.id, 'employee_id': self.employee.id, **vals,
        })

    def test_assign_button_opens_assignment_form(self):
        action = self.asset.action_open_assign_wizard()
        self.assertEqual(action['res_model'], 'it.asset.assignment')
        self.assertEqual(action['target'], 'new')

    def test_saving_assignment_assigns_asset(self):
        assignment = self._assign(condition_out='new', accessories='Charger')
        self.assertEqual(self.asset.state, 'assigned')
        self.assertEqual(self.asset.employee_id, self.employee)
        self.assertEqual(assignment.state, 'active')

    def test_cannot_assign_unavailable_asset(self):
        self._assign()
        with self.assertRaises(UserError):
            self._assign()

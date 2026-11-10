import base64

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged

# 1x1 transparent PNG
PNG = base64.b64encode(base64.b64decode(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII='
))


@tagged('post_install', '-at_install')
class TestAssetHandover(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        category = cls.env['it.asset.category'].search([], limit=1) or \
            cls.env['it.asset.category'].create({'name': 'Laptop'})
        cls.asset = cls.env['it.asset'].create({
            'name': 'Test Laptop', 'category_id': category.id, 'serial_number': 'SN-001',
        })
        cls.employee = cls.env['hr.employee'].create({'name': 'Asset Holder'})

    def _assign(self, **vals):
        return self.env['it.asset.assignment'].create({
            'asset_id': self.asset.id, 'employee_id': self.employee.id, **vals,
        })

    def test_signature_dates_stamped_and_cleared(self):
        assignment = self._assign()
        self.assertFalse(assignment.is_fully_signed)
        assignment.write({'employee_signature': PNG, 'employee_signature_method': 'wacom_pad'})
        self.assertTrue(assignment.employee_signed_date)
        self.assertFalse(assignment.is_fully_signed)
        assignment.write({'it_signature': PNG})
        self.assertTrue(assignment.is_fully_signed)
        assignment.action_clear_signatures()
        self.assertFalse(assignment.employee_signed_date)
        self.assertFalse(assignment.it_signed_date)

    def test_handover_report_renders(self):
        assignment = self._assign(employee_signature=PNG)
        html, _fmt = self.env['ir.actions.report']._render_qweb_html(
            'KSW_asset_handover.report_it_asset_handover', assignment.ids)
        html = html.decode()
        self.assertIn('Asset Handover Form', html)
        self.assertIn('SN-001', html)
        self.assertIn('data:image/png;base64', html)

    def test_returned_assignment_signatures_locked(self):
        assignment = self._assign(employee_signature=PNG)
        assignment.state = 'returned'
        with self.assertRaises(UserError):
            assignment.action_clear_signatures()

    def test_app_is_it_team_only(self):
        Menu = self.env['ir.ui.menu']
        root = self.env.ref('KSW_asset_handover.menu_it_assets_root')
        assets = self.env.ref('KSW_helpdesk.menu_helpdesk_assets_all')
        self.assertEqual(assets.parent_id, root)
        agent = self.env['res.users'].create({
            'name': 'IT Assets Agent', 'login': 'it_assets_agent',
            'group_ids': [(6, 0, [self.env.ref('KSW_helpdesk.group_helpdesk_agent').id])],
        })
        employee = self.env['res.users'].create({
            'name': 'IT Assets Employee', 'login': 'it_assets_employee',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id])],
        })
        self.assertIn(root.id, Menu.with_user(agent)._visible_menu_ids())
        self.assertIn(assets.id, Menu.with_user(agent)._visible_menu_ids())
        self.assertNotIn(root.id, Menu.with_user(employee)._visible_menu_ids())
        # Employees keep their read-only "My Assets" in the Helpdesk.
        self.assertIn(self.env.ref('KSW_helpdesk.menu_helpdesk_my_assets').id,
                      Menu.with_user(employee)._visible_menu_ids())

    def test_uninstall_hook_moves_menus_back(self):
        from odoo.addons.KSW_asset_handover import uninstall_hook
        uninstall_hook(self.env)
        self.assertEqual(self.env.ref('KSW_helpdesk.menu_helpdesk_assets_all').parent_id,
                         self.env.ref('KSW_helpdesk.menu_helpdesk_assets'))
        self.assertEqual(self.env.ref('KSW_helpdesk.menu_helpdesk_asset_categories').parent_id,
                         self.env.ref('KSW_helpdesk.menu_helpdesk_config'))
        root = self.env.ref('KSW_asset_handover.menu_it_assets_root')
        self.assertFalse((root.child_id | root.child_id.child_id).filtered('action'))

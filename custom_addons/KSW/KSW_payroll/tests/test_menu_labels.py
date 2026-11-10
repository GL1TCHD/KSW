from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestMenuArabicLabels(TransactionCase):

    def setUp(self):
        super().setUp()
        if not self.env['res.lang']._lang_get('ar_001'):
            self.skipTest('Arabic not installed')

    def test_payroll_lists_are_told_apart(self):
        Menu = self.env['ir.ui.menu'].with_context(lang='ar_001')
        slips = Menu.browse(self.env.ref('om_hr_payroll.menu_department_tree').id).name
        batches = Menu.browse(self.env.ref('om_hr_payroll.menu_hr_payslip_run').id).name
        settings = Menu.browse(self.env.ref('om_hr_payroll.menu_hr_payroll_global_settings').id).name
        config = Menu.browse(self.env.ref('om_hr_payroll.menu_hr_payroll_configuration').id).name
        self.assertNotEqual(slips, batches)
        self.assertNotEqual(settings, config)

    def test_helper_writes_arabic_only_and_skips_unknown(self):
        menu = self.env.ref('om_hr_payroll.hr_menu_contract')
        english = menu.with_context(lang='en_US').name
        self.env['ir.ui.menu']._ksw_set_menu_arabic({
            'om_hr_payroll.hr_menu_contract': 'اختبار',
            'no_such_module.no_such_menu': 'x',
        })
        self.assertEqual(menu.with_context(lang='ar_001').name, 'اختبار')
        self.assertEqual(menu.with_context(lang='en_US').name, english)

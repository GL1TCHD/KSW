import re

from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestBackendTheme(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        bundle = cls.env['ir.qweb']._get_asset_bundle('web.assets_backend', css=True, js=False)
        cls.css = bundle.preprocess_css()
        cls.css_errors = bundle.css_errors

    def test_bundle_compiles(self):
        self.assertFalse(self.css_errors, 'web.assets_backend SCSS failed: %s' % self.css_errors)

    def test_light_navbar(self):
        backgrounds = [
            m.group(1).strip()
            for rule in re.findall(r'\.o_main_navbar\s*\{([^}]*)\}', self.css)
            for m in [re.search(r'(?<![-\w])background:([^;]+);', rule)] if m
        ]
        self.assertTrue(backgrounds, 'no background on .o_main_navbar')
        self.assertIn(backgrounds[0].lower(), ('#fff', '#ffffff', 'white'))

    def test_company_colours_replace_muk_slate(self):
        css = self.css.lower()
        self.assertIn('#0b7899', css, 'teal primary missing')
        self.assertIn('#124081', css, 'navy brand missing')

    def test_arabic_font_scoped_to_arabic(self):
        face = re.search(r'@font-face\s*\{[^}]*KSW Arabic[^}]*\}', self.css)
        self.assertTrue(face, 'KSW Arabic @font-face missing')
        self.assertIn('Tajawal', face.group(0))
        self.assertIn('unicode-range', face.group(0))

    def test_chatter_buttons_not_red(self):
        self.assertRegex(self.css, r'\.o-mail-Chatter-sendMessage\.btn-danger')

    def test_muk_strings_in_arabic(self):
        if not self.env['res.lang']._lang_get('ar_001'):
            self.skipTest('Arabic not installed')
        per_module, _ = self.env['ir.http']._get_translations_for_webclient(
            ['KSW_backend_theme'], 'ar_001')
        terms = {m['id']: m['string'] for m in per_module['KSW_backend_theme']['messages']}
        self.assertEqual(terms.get('Internal'), 'داخلي')
        field = self.env.ref('muk_web_appsbar.field_res_users__sidebar_type')
        self.assertEqual(field.with_context(lang='ar_001').field_description, 'شريط التطبيقات الجانبي')


@tagged('post_install', '-at_install')
class TestEveryMenuHasArabic(TransactionCase):
    """A menu without Arabic shows English inside the Arabic UI (34 did, Nov 2026)."""

    def test_no_active_menu_without_arabic(self):
        if not self.env['res.lang']._lang_get('ar_001'):
            self.skipTest('Arabic not installed')
        self.env.cr.execute("""
            SELECT coalesce(d.module || '.' || d.name, m.id::text), m.name->>'en_US'
              FROM ir_ui_menu m
         LEFT JOIN ir_model_data d ON d.model = 'ir.ui.menu' AND d.res_id = m.id
             WHERE m.active AND m.name->>'ar_001' IS NULL
             ORDER BY 1
        """)
        missing = self.env.cr.fetchall()
        self.assertFalse(missing, 'Menus with no Arabic label: %s' % missing)

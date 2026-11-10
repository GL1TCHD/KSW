from odoo.tests import HttpCase, TransactionCase, new_test_user, tagged


@tagged('post_install', '-at_install')
class TestDarkBundles(TransactionCase):

    def _css(self, bundle_name):
        bundle = self.env['ir.qweb']._get_asset_bundle(bundle_name, css=True, js=False)
        css = bundle.preprocess_css()
        self.assertFalse(bundle.css_errors, '%s: %s' % (bundle_name, bundle.css_errors))
        return css.lower()

    def test_dark_bundle_is_dark(self):
        css = self._css('web.assets_web_dark')
        self.assertIn('#121519', css, 'dark page colour missing')
        self.assertIn('#5cc6e4', css, 'dark-mode teal missing')

    def test_light_bundle_stays_light(self):
        css = self._css('web.assets_web')
        self.assertNotIn('#121519', css)

    def test_lazy_dark_bundle_compiles(self):
        self._css('web.assets_backend_lazy_dark')


@tagged('post_install', '-at_install')
class TestColorSchemeCookie(HttpCase):

    def test_cookie_picks_the_stylesheet(self):
        new_test_user(self.env, login='ksw_dark', password='ksw_dark_pw1', groups='base.group_user')
        self.authenticate('ksw_dark', 'ksw_dark_pw1')
        light = self.url_open('/odoo').text
        self.assertIn('web.assets_web.', light)
        self.assertNotIn('web.assets_web_dark', light)
        self.opener.cookies.set('color_scheme', 'dark')
        dark = self.url_open('/odoo').text
        self.assertIn('web.assets_web_dark', dark)


@tagged('post_install', '-at_install')
class TestFrontendBundle(TransactionCase):

    def test_login_styles_compile(self):
        bundle = self.env['ir.qweb']._get_asset_bundle('web.assets_frontend', css=True, js=False)
        css = bundle.preprocess_css()
        self.assertFalse(bundle.css_errors)
        self.assertIn('oe_login_form', css)

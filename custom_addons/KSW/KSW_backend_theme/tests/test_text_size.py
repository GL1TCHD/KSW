from odoo.tests import HttpCase, TransactionCase, new_test_user, tagged


@tagged('post_install', '-at_install')
class TestTextSize(HttpCase):

    def _body_has_large(self, user):
        self.authenticate(user.login, user.login + 'Pw1!')
        return 'ksw_text_large' in self.url_open('/odoo').text

    def test_automatic_follows_language(self):
        if not self.env['res.lang']._lang_get('ar_001'):
            self.skipTest('Arabic not installed')
        en = new_test_user(self.env, login='ksw_txt_en', password='ksw_txt_enPw1!', groups='base.group_user')
        ar = new_test_user(self.env, login='ksw_txt_ar', password='ksw_txt_arPw1!', groups='base.group_user')
        ar.lang = 'ar_001'
        self.assertFalse(self._body_has_large(en))
        self.assertTrue(self._body_has_large(ar))

    def test_explicit_choice_wins(self):
        user = new_test_user(self.env, login='ksw_txt_x', password='ksw_txt_xPw1!', groups='base.group_user')
        user.with_user(user).write({'ksw_text_size': 'large'})   # a self-write from Preferences
        self.assertTrue(self._body_has_large(user))
        user.ksw_text_size = 'standard'
        self.assertFalse(self._body_has_large(user))

    def test_preferences_save_reloads_the_page(self):
        user = new_test_user(self.env, login='ksw_txt_r', groups='base.group_user')
        self.assertEqual(user.with_user(user).preference_save()['tag'], 'reload')


@tagged('post_install', '-at_install')
class TestHomeScrolls(TransactionCase):

    def test_home_overrides_core_overflow(self):
        bundle = self.env['ir.qweb']._get_asset_bundle('web.assets_backend', css=True, js=False)
        css = bundle.preprocess_css()
        self.assertRegex(css, r'html \.o_web_client\s*>\s*\.o_action_manager\s*>\s*\.ksw_home\.o_action\s*\{[^}]*overflow-y:\s*auto')

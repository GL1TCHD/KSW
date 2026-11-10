from odoo.tests import HttpCase, new_test_user, tagged


@tagged('post_install', '-at_install')
class TestQuickTour(HttpCase):

    def test_shown_once_per_account(self):
        user = new_test_user(self.env, login='ksw_tour_u', password='ksw_tour_uPw1!', groups='base.group_user')
        self.assertFalse(user.ksw_tour_done)
        self.authenticate('ksw_tour_u', 'ksw_tour_uPw1!')
        info = self.make_jsonrpc_request('/web/session/get_session_info')
        self.assertIs(info['ksw_tour_done'], False)
        self.env['res.users'].with_user(user).ksw_tour_mark_done()
        self.assertTrue(user.ksw_tour_done)
        info = self.make_jsonrpc_request('/web/session/get_session_info')
        self.assertIs(info['ksw_tour_done'], True)

    def test_mark_done_touches_only_the_caller(self):
        a = new_test_user(self.env, login='ksw_tour_a', groups='base.group_user')
        b = new_test_user(self.env, login='ksw_tour_b', groups='base.group_user')
        self.env['res.users'].with_user(a).ksw_tour_mark_done()
        self.assertTrue(a.ksw_tour_done)
        self.assertFalse(b.ksw_tour_done)

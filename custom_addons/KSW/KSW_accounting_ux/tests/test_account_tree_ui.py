from odoo.tests import HttpCase, tagged

# Roots only -> All -> search narrows and opens the path -> leaf opens its items.
TREE_JS = """
    const t0 = Date.now();
    const rows = () => document.querySelectorAll('.o_ksw_tree_body .o_ksw_tree_row');
    const fail = (msg) => console.error(msg);
    let step = 0, roots = 0, all = 0;
    const tick = () => {
        if (Date.now() - t0 > 40000) { return fail('account tree stuck at step ' + step); }
        if (step === 0 && rows().length) {
            roots = rows().length;
            if (document.querySelectorAll('.o_ksw_tree_body .o_ksw_tree_leaf').length) {
                return fail('leaves visible before anything was opened');
            }
            [...document.querySelectorAll('.o_ksw_tree_head button')]
                .find((b) => b.textContent.trim() === 'All').click();
            step = 1;
        } else if (step === 1 && rows().length > roots) {
            all = rows().length;
            const input = document.querySelector('.o_ksw_tree_search input');
            input.value = 'ZZ Tree Leaf';
            input.dispatchEvent(new Event('input', { bubbles: true }));
            step = 2;
        } else if (step === 2 && rows().length < all) {
            const hits = document.querySelectorAll('.o_ksw_tree_body .o_ksw_tree_hit');
            const leaf = document.querySelector('.o_ksw_tree_body .o_ksw_tree_leaf.o_ksw_tree_hit');
            // The match plus its two ancestor groups, nothing else
            if (hits.length !== 1 || !leaf || rows().length !== 3) {
                return fail('search shows ' + rows().length + ' rows, ' + hits.length + ' hits');
            }
            leaf.querySelector('.o_ksw_tree_actions button').click();
            step = 3;
        } else if (step === 3 && document.querySelector('.o_list_view')) {
            console.log('test successful');
            return;
        }
        setTimeout(tick, 250);
    };
    tick();
"""

LIST_BUTTON_JS = """
    const t0 = Date.now();
    let clicked = false;
    const tick = () => {
        const btn = [...document.querySelectorAll('.o_control_panel button, .o_list_buttons button')]
            .find((b) => b.textContent.includes('Tree View'));
        if (!clicked && btn) { btn.click(); clicked = true; }
        if (document.querySelector('.o_ksw_tree_body .o_ksw_tree_row')) {
            console.log('test successful');
        } else if (Date.now() - t0 > 20000) {
            console.error('Tree View button missing or tree never opened (clicked: ' + clicked + ')');
        } else { setTimeout(tick, 300); }
    };
    tick();
"""


@tagged('post_install', '-at_install')
class TestAccountTreeUi(HttpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Group = cls.env['account.group']
        Group.create({'name': 'ZZ Tree Root', 'code_prefix_start': '97'})
        Group.create({'name': 'ZZ Tree Child', 'code_prefix_start': '9701'})
        cls.env['account.account'].create(
            {'code': '970101', 'name': 'ZZ Tree Leaf', 'account_type': 'expense'})

    def test_tree_expands_searches_and_opens_items(self):
        self.browser_js('/odoo/account-tree', TREE_JS, login='admin', timeout=90)

    def test_chart_of_accounts_list_has_tree_button(self):
        self.browser_js('/odoo/action-account.action_account_form', LIST_BUTTON_JS,
                        login='admin', timeout=60)

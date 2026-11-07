from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestAccountTree(TransactionCase):
    """The tree page's payload: groups with parents, leaves with their group."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        Group = cls.env['account.group']
        cls.root = Group.create({'name': 'ZZ Root', 'code_prefix_start': '97'})
        cls.child = Group.create({'name': 'ZZ Child', 'code_prefix_start': '9701'})
        cls.leaf = cls.env['account.account'].create({
            'code': '970101', 'name': 'ZZ Leaf', 'account_type': 'expense'})
        cls.loose = cls.env['account.account'].create({
            'code': '989898', 'name': 'ZZ Loose', 'account_type': 'expense'})

    def _tree(self):
        return self.env['account.account'].ksw_get_account_tree()

    def test_hierarchy(self):
        data = self._tree()
        groups = {g['id']: g for g in data['groups']}
        accounts = {a['id']: a for a in data['accounts']}
        self.assertEqual(groups[self.child.id]['parent_id'], self.root.id)
        self.assertFalse(groups[self.root.id]['parent_id'])
        # The leaf sits in the deepest group its code falls in
        self.assertEqual(accounts[self.leaf.id]['group_id'], self.child.id)
        self.assertFalse(accounts[self.loose.id]['group_id'])
        self.assertTrue(accounts[self.leaf.id]['type'])

    def test_balance_is_posted_only(self):
        journal = self.env['account.journal'].search(
            [('type', '=', 'general'), ('company_id', '=', self.company.id)], limit=1)
        other = self.env['account.account'].create({
            'code': '970102', 'name': 'ZZ Other', 'account_type': 'expense'})
        def entry():
            return self.env['account.move'].create({
                'move_type': 'entry', 'journal_id': journal.id,
                'line_ids': [
                    (0, 0, {'account_id': self.leaf.id, 'debit': 150.0}),
                    (0, 0, {'account_id': other.id, 'credit': 150.0}),
                ]})
        entry().action_post()
        entry()  # draft: must not count
        accounts = {a['id']: a for a in self._tree()['accounts']}
        self.assertEqual(accounts[self.leaf.id]['balance'], 150.0)
        self.assertEqual(accounts[other.id]['balance'], -150.0)

    def test_other_company_groups_excluded(self):
        other = self.env['res.company'].create({'name': 'ZZ Other Co'})
        group = self.env['account.group'].create(
            {'name': 'ZZ Foreign', 'code_prefix_start': '96', 'company_id': other.id})
        ids = {g['id'] for g in self._tree()['groups']}
        self.assertNotIn(group.id, ids)
        self.assertIn(self.root.id, ids)

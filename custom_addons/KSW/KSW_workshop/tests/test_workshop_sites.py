from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestWorkshopSites(TransactionCase):
    """Three workshops (Nabiya, Tabuk, Al-Hayat): a request goes to the home
    workshop of the requester's department; each workshop's manager and
    technicians reach only its requests; the GM reviews all of them."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.nabiya = cls.env.ref('KSW_workshop.workshop_nabiya')
        cls.tabuk = cls.env.ref('KSW_workshop.workshop_tabuk')
        Dept = cls.env['hr.department']
        cls.dept_tabuk = Dept.create({'name': 'Tabuk Ops (test)', 'x_workshop_id': cls.tabuk.id})
        cls.dept_tabuk_sub = Dept.create({'name': 'Tabuk Isuzu (test)', 'parent_id': cls.dept_tabuk.id})
        cls.dept_plain = Dept.create({'name': 'No workshop (test)'})
        Users = cls.env['res.users'].with_context(no_reset_password=True)

        def _user(login, groups, department=None, workshops=()):
            user = Users.create({
                'name': login, 'login': login, 'email': f'{login}@sites.test',
                'group_ids': [(6, 0, [cls.env.ref(g).id for g in groups])],
                'x_workshop_ids': [(6, 0, [w.id for w in workshops])]})
            cls.env['hr.employee'].create({
                'name': login, 'user_id': user.id,
                'department_id': department.id if department else False})
            return user

        requester = ('KSW_workshop.group_workshop_requester',)
        cls.req_tabuk = _user('site_req_tbk', requester, cls.dept_tabuk_sub)
        cls.req_nabiya = _user('site_req_nab', requester, cls.dept_plain)
        cls.mgr_tabuk = _user('site_mgr_tbk', ('KSW_workshop.group_workshop_manager',), workshops=[cls.tabuk])
        cls.mgr_nabiya = _user('site_mgr_nab', ('KSW_workshop.group_workshop_manager',))
        cls.gm = _user('site_gm', ('KSW_workshop.group_workshop_reviewer',))
        cls.tech_tabuk = _user('site_tech_tbk', ('KSW_workshop.group_workshop_technician',))
        cls.tech_tabuk_reg = cls.env['ksw.workshop.technician'].create({
            'employee_id': cls.tech_tabuk.employee_id.id, 'workshop_id': cls.tabuk.id})
        cls.tech_nabiya_reg = cls.env['ksw.workshop.technician'].create({
            'name': 'Nabiya hand (test)', 'workshop_id': cls.nabiya.id})

        def _request(user, plate):
            vehicle = cls.env['ksw.fleet.vehicle'].create({
                'name': plate, 'vehicle_type': 'isuzu', 'driver_id': user.employee_id.id})
            return cls.env['ksw.workshop.request'].with_user(user).create({
                'vehicle_type': 'isuzu', 'vehicle_id': vehicle.id,
                'driver_id': user.employee_id.id, 'description': 'Oil change'}).sudo()

        cls.r_tabuk = _request(cls.req_tabuk, 'SITE-T1')
        cls.r_nabiya = _request(cls.req_nabiya, 'SITE-N1')

    # Routing ---------------------------------------------------------------
    def test_request_follows_the_department_tree(self):
        self.assertEqual(self.r_tabuk.workshop_id, self.tabuk, 'sub-department inherits its parent')
        self.assertEqual(self.r_nabiya.workshop_id, self.nabiya, 'no home workshop: the main one')

    def test_requester_cannot_send_it_elsewhere(self):
        with self.assertRaises(UserError):
            self.r_tabuk.with_user(self.req_tabuk).write({'workshop_id': self.nabiya.id})

    def test_manager_can_send_his_request_to_another_workshop(self):
        self.r_tabuk.with_user(self.mgr_tabuk).write({'workshop_id': self.nabiya.id})
        self.assertEqual(self.r_tabuk.workshop_id, self.nabiya)

    # Scope -----------------------------------------------------------------
    def test_each_manager_sees_only_his_workshop(self):
        for manager, own, other in ((self.mgr_tabuk, self.r_tabuk, self.r_nabiya),
                                    (self.mgr_nabiya, self.r_nabiya, self.r_tabuk)):
            found = self.env['ksw.workshop.request'].with_user(manager).search(
                [('id', 'in', (own | other).ids)])
            self.assertEqual(found, own, manager.login)
            with self.assertRaises(AccessError):
                other.with_user(manager).read(['name'])

    def test_manager_cannot_triage_another_workshop(self):
        with self.assertRaises(AccessError):
            self.r_nabiya.with_user(self.mgr_tabuk).action_start()

    def test_gm_reviews_every_workshop_and_can_narrow(self):
        both = self.r_tabuk | self.r_nabiya
        Request = self.env['ksw.workshop.request']
        self.assertEqual(Request.with_user(self.gm).search([('id', 'in', both.ids)]), both)
        self.gm.x_workshop_ids = self.tabuk
        self.assertEqual(Request.with_user(self.gm).search([('id', 'in', both.ids)]), self.r_tabuk)

    def test_technician_sees_his_workshop_in_progress_only(self):
        self.r_tabuk.with_user(self.mgr_tabuk).action_start()
        self.r_nabiya.with_user(self.mgr_nabiya).action_start()
        found = self.env['ksw.workshop.request'].with_user(self.tech_tabuk).search(
            [('id', 'in', (self.r_tabuk | self.r_nabiya).ids)])
        self.assertEqual(found, self.r_tabuk)

    def test_labor_line_names_only_this_workshops_technicians(self):
        self.r_tabuk.with_user(self.mgr_tabuk).action_start()
        Line = self.env['ksw.workshop.labor.line']
        Line.create({'request_id': self.r_tabuk.id, 'description': 'Filter',
                     'technician_id': self.tech_tabuk_reg.id})
        external = self.env['ksw.workshop.technician'].search([('is_external', '=', True)], limit=1)
        if external:
            Line.create({'request_id': self.r_tabuk.id, 'description': 'Sent out',
                         'technician_id': external.id})
        with self.assertRaises(ValidationError):
            Line.create({'request_id': self.r_tabuk.id, 'description': 'Wrong shop',
                         'technician_id': self.tech_nabiya_reg.id})

    # Notifications ---------------------------------------------------------
    def test_only_the_workshops_own_manager_is_notified(self):
        message = self.r_tabuk.message_ids.filtered(lambda m: 'New workshop request' in (m.body or ''))
        self.assertIn(self.mgr_tabuk.partner_id, message.partner_ids)
        self.assertNotIn(self.mgr_nabiya.partner_id, message.partner_ids)
        message = self.r_nabiya.message_ids.filtered(lambda m: 'New workshop request' in (m.body or ''))
        self.assertIn(self.mgr_nabiya.partner_id, message.partner_ids)
        self.assertNotIn(self.mgr_tabuk.partner_id, message.partner_ids)

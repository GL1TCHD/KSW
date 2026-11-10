from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestWorkshopFollowsDepartment(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Department = cls.env['hr.department']
        cls.dept_a = Department.create({'name': 'FW Alpha', 'x_fleet_holder': True})
        cls.dept_b = Department.create({'name': 'FW Bravo', 'x_fleet_holder': True})
        cls.dept_maint = Department.create({'name': 'FW Maintenance'})
        Employee = cls.env['hr.employee']
        cls.supervisor_a = Employee.create({'name': 'FW Sup A', 'department_id': cls.dept_a.id})
        cls.driver_a = Employee.create({'name': 'FW Driver A', 'department_id': cls.dept_a.id,
                                        'parent_id': cls.supervisor_a.id})
        user_b = cls.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'FW Manager B', 'login': 'fw_manager_b',
            'group_ids': [(6, 0, [cls.env.ref('base.group_user').id])],
            'x_fleet_department_ids': [(6, 0, cls.dept_b.ids)]})
        cls.manager_b = Employee.create({'name': 'FW Manager B', 'department_id': cls.dept_b.id,
                                         'user_id': user_b.id})
        cls.maint = Employee.create({'name': 'FW Maint', 'department_id': cls.dept_maint.id})
        cls.maint_driver = Employee.create({'name': 'FW Maint Driver', 'department_id': cls.dept_maint.id,
                                            'parent_id': cls.maint.id})
        cls.truck = cls.env['ksw.fleet.vehicle'].create({
            'name': 'FW-IS1', 'vehicle_type': 'isuzu', 'driver_id': cls.driver_a.id,
            'x_department_id': cls.dept_a.id})
        if 'x_bas_driver_id' in cls.truck._fields:
            cls.truck.x_bas_driver_id = False

    def _scope(self, employee):
        return self.env['ksw.fleet.vehicle']._workshop_scope_for_employees(employee)

    def test_scope_moves_with_the_department(self):
        self.assertIn(self.truck.id, self._scope(self.supervisor_a))
        self.assertNotIn(self.truck.id, self._scope(self.manager_b))
        self.truck.write({'x_department_id': self.dept_b.id})   # e.g. a received transfer
        # Same driver still on it, but it is Bravo's now.
        self.assertNotIn(self.truck.id, self._scope(self.supervisor_a))
        self.assertIn(self.truck.id, self._scope(self.manager_b))

    def test_outside_vehicle_departments_unchanged(self):
        self.truck.write({'driver_id': self.maint_driver.id})
        self.assertIn(self.truck.id, self._scope(self.maint))

    def test_hand_edit_still_wins(self):
        self.truck.write({'x_department_id': self.dept_b.id})
        self.supervisor_a.sudo().write({'x_workshop_vehicle_added_ids': [(4, self.truck.id)]})
        self.assertIn(self.truck.id, self._scope(self.supervisor_a))

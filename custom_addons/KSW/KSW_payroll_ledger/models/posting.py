"""Where each salary figure is booked: configuration, not code.

Built the way SAP and Oracle separate the questions (Nov 2026):

* **what** the figure is — the salary rule's Posting Category (SAP's
  symbolic account): Housing, Transport, Other allowance… A rule without one
  is the wage;
* **who** the employee is for the books — his Posting Group (SAP's employee
  grouping for account determination): Main Office, Jubail Camp, Transport,
  Separate Salaries (M), Al-Hayat Factory… set on the department, inherited
  down the tree, overridden on the employee;
* the **Posting Map** answers the two together: group × category → account,
  a row without a category meaning "everything else". A branch that books
  the whole salary on one account is one row; the main office is one row per
  allowance;
* an employee whose figures go somewhere else for a reason of his own has
  **Posting Exceptions** (Oracle's assignment-level costing);
* whose money it is — the **Entity** of the bank account that paid him
  (Al-Kawther, Al-Hayat), with its own payable — and whose staff he is — the
  entity of his department. **Paid for Another Entity** says what happens
  when they differ: a posting group to book him in, and/or the current
  accounts each side keeps with the other.

A figure no row answers goes to the suspense account in Settings, named on
the batch, so the batch still posts (Oracle's suspense costing); without a
suspense account the employee is held out, as before.
"""
from odoo import api, fields, models

DEDUCTION_TREATMENTS = [
    ('spread', 'Spread over the earnings'),
    ('basic', 'Against the basic wage only'),
    ('own_account', "Employee's own account"),
]


class KswPayrollEntity(models.Model):
    _name = 'ksw.payroll.entity'
    _description = 'Payroll Entity (whose money, whose staff)'
    _order = 'sequence, id'

    name = fields.Char(required=True, translate=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one('res.company', required=True, default=lambda s: s.env.company)
    is_default = fields.Boolean(
        string='Default Entity',
        help='The entity of a department or bank account that names none.')
    payable_account_id = fields.Many2one(
        'account.account', string='Salaries Payable', required=True,
        help='Credited with the net of every salary this entity pays.')
    default_group_id = fields.Many2one(
        'ksw.payroll.posting.group', string='Default Posting Group', required=True,
        help="For this entity's staff whose department names no posting group.")
    note = fields.Text()


class KswPayrollIntercompany(models.Model):
    _name = 'ksw.payroll.intercompany'
    _description = 'Salary Paid for Another Entity'
    _rec_name = 'payer_id'

    payer_id = fields.Many2one('ksw.payroll.entity', string='Paid by', required=True, ondelete='cascade')
    staff_entity_id = fields.Many2one(
        'ksw.payroll.entity', string='Staff of', required=True, ondelete='cascade')
    posting_group_id = fields.Many2one(
        'ksw.payroll.posting.group', string='Book them in',
        help='Posting group for these employees when this entity pays them. Empty: their own.')
    payer_account_id = fields.Many2one(
        'account.account', string="Due from the Staff's Entity",
        help="Debited with the net, in the paying entity's books (Al-Hayat paying a "
             "Kawther employee: 2101030003). Empty, with the next one: no current accounts.")
    staff_account_id = fields.Many2one(
        'account.account', string='Due to the Payer',
        help="Credited with the net instead of salaries payable, in the staff entity's "
             "books (1208010010).")
    note = fields.Text()

    _payer_staff_unique = models.Constraint(
        'unique(payer_id, staff_entity_id)', 'One row per paying entity and staff entity.')


class KswPayrollPostingCategory(models.Model):
    _name = 'ksw.payroll.posting.category'
    _description = 'Payroll Posting Category'
    _order = 'sequence, id'

    name = fields.Char(required=True, translate=True)
    code = fields.Char(required=True)
    sequence = fields.Integer(default=10)
    rule_ids = fields.One2many('hr.salary.rule', 'x_posting_category_id', string='Salary Rules')
    note = fields.Text()

    _code_unique = models.Constraint('unique(code)', 'The category code must be unique.')


class KswPayrollPostingGroup(models.Model):
    _name = 'ksw.payroll.posting.group'
    _description = 'Payroll Posting Group'
    _order = 'sequence, name'

    name = fields.Char(required=True, translate=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    map_ids = fields.One2many('ksw.payroll.posting.map', 'group_id', string='Accounts', copy=True)
    department_ids = fields.One2many('hr.department', 'x_posting_group_id', string='Departments')
    employee_count = fields.Integer(compute='_compute_employee_count')
    note = fields.Text()

    def _compute_employee_count(self):
        Employee = self.env['hr.employee'].sudo()
        for group in self:
            group.employee_count = Employee.search_count([('x_posting_group_id', '=', group.id)])


class KswPayrollPostingMap(models.Model):
    _name = 'ksw.payroll.posting.map'
    _description = 'Payroll Posting Map'
    _order = 'group_id, category_id desc, id'

    group_id = fields.Many2one('ksw.payroll.posting.group', required=True, ondelete='cascade')
    category_id = fields.Many2one(
        'ksw.payroll.posting.category', string='Category',
        help='Empty: the wage and every category without a row of its own.')
    account_id = fields.Many2one('account.account', required=True)

    _group_category_unique = models.Constraint(
        'unique(group_id, category_id)', 'One account per posting group and category.')


class KswPayrollEmployeePosting(models.Model):
    _name = 'ksw.payroll.employee.posting'
    _description = 'Payroll Posting Exception'

    employee_id = fields.Many2one('hr.employee', required=True, ondelete='cascade')
    category_id = fields.Many2one(
        'ksw.payroll.posting.category', string='Category',
        help='Empty: his wage and every category without a row of its own.')
    account_id = fields.Many2one('account.account', required=True)
    note = fields.Char(string='Reason')

    _employee_category_unique = models.Constraint(
        'unique(employee_id, category_id)', 'One exception per employee and category.')


class KswPayrollPosting(models.AbstractModel):
    """Answers "which account" from the tables above. Read as superuser:
    whoever confirms a batch need not read accounting configuration."""
    _name = 'ksw.payroll.posting'
    _description = 'Payroll Account Determination'

    @api.model
    def _default_entity(self, company):
        return self.env['ksw.payroll.entity'].sudo().search(
            [('company_id', '=', company.id), ('is_default', '=', True)], limit=1)

    @api.model
    def _staff_entity(self, employee, company):
        dept = employee.sudo().department_id
        while dept:
            if dept.x_ledger_entity_id:
                return dept.x_ledger_entity_id
            dept = dept.parent_id
        return self._default_entity(company)

    @api.model
    def _payer_entity(self, bank, company):
        return bank.sudo().x_ledger_entity_id or self._default_entity(company)

    @api.model
    def _intercompany(self, payer, staff):
        if not payer or not staff or payer == staff:
            return self.env['ksw.payroll.intercompany']
        return self.env['ksw.payroll.intercompany'].sudo().search(
            [('payer_id', '=', payer.id), ('staff_entity_id', '=', staff.id)], limit=1)

    @api.model
    def _posting_group(self, employee, staff, payer):
        intercompany = self._intercompany(payer, staff)
        if intercompany.posting_group_id:
            return intercompany.posting_group_id
        employee = employee.sudo()
        if employee.x_posting_group_id:
            return employee.x_posting_group_id
        dept = employee.department_id
        while dept:
            if dept.x_posting_group_id:
                return dept.x_posting_group_id
            dept = dept.parent_id
        return staff.default_group_id

    @api.model
    def _account(self, employee, group, category):
        """His exceptions first, then his group's map; in each, the row for the
        category, else the row without one. Empty when nothing answers."""
        for rows in (employee.sudo().x_posting_exception_ids, group.sudo().map_ids):
            row = (rows.filtered(lambda r: r.category_id == category)[:1] if category else rows.browse()) \
                or rows.filtered(lambda r: not r.category_id)[:1]
            if row:
                return row.account_id
        return self.env['account.account']

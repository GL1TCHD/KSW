"""The accountant's review of the reposted Aug–Oct entries (Nov 2026).

* Equipment Rental Department is the transport branch: 3206010001.
* GOSI is credited to the employee's own account when he has one.
* AHMED NAZMI: basic on the separate salary account, allowances on the camp.
* Three own accounts found in BAS, missing on the employee.

The Jubail-camp allowances and the pro-rata deductions are code, not data.
Each value is written only where nothing is set.
"""
import logging

from odoo import SUPERUSER_ID, api

from odoo.addons.KSW_payroll_ledger.hooks import PERSONAL_ACCOUNT_RULES

_logger = logging.getLogger(__name__)

# employee no. -> his own account in BAS
OWN_ACCOUNTS = {
    '1526': '1205010009',   # AQEEL MOHAMMED AL ZAHER   — سلف عقيل محمد الزاهر
    '1468': '1205010369',   # MOHAMMED HABEEB M ALBASTI — سلف محمد حبيب البسطي
    '1475': '2111010015',   # Taha Hussein Al-Shakhs    — ح جاري/طه حسين
}
ALLOWANCE_ACCOUNTS = {'842': '3204010001'}   # AHMED NAZMI: 8,500 on M, 2,750 on the camp


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {'active_test': False, 'lang': 'en_US'})
    Account, Employee = env['account.account'], env['hr.employee']
    rules = env['hr.salary.rule'].search([('code', 'in', PERSONAL_ACCOUNT_RULES)])
    rules.write({'x_ledger_to_personal_account': True})
    for company in env['res.company'].search([]):
        def account(code):
            return Account.search([('company_ids', 'in', company.id), ('code', '=', code)], limit=1)
        transport = account('3206010001')
        if not transport:
            continue
        depts = env['hr.department'].search([
            ('name', '=', 'Equipment Rental Department'), ('x_wage_account_id', '=', False)])
        depts.write({'x_wage_account_id': transport.id})
        for number, code in ALLOWANCE_ACCOUNTS.items():
            emp = Employee.search([('x_employee_no', '=', number)])
            if len(emp) == 1 and not emp.x_allowance_account_id and account(code):
                emp.x_allowance_account_id = account(code)
        for number, code in OWN_ACCOUNTS.items():
            emp = Employee.search([('x_employee_no', '=', number)])
            if len(emp) == 1 and not (emp.x_loan_acc_no or '').strip() and account(code):
                emp.x_loan_acc_no = code
            elif len(emp) != 1:
                _logger.warning('KSW_payroll_ledger: employee no. %s not found once, own account %s not set', number, code)
        _logger.info('KSW_payroll_ledger: 3206010001 on %s; GOSI rules %s',
                     ', '.join(depts.mapped('name')) or 'no department', ', '.join(rules.mapped('code')))

"""The mapping read off BAS's September 2026 salary vouchers (26009158/26009187).

Seeded once, only where nothing is set and the account exists: after
install it is configuration, maintained on the rule and the department.
"""
import logging

_logger = logging.getLogger(__name__)

RULE_ACCOUNTS = {
    'HRA': '3201010002',             # بدل سكن العاملين
    'Travel': '3201010003',          # بدل انتقال العاملين
    'Mobile': '3201010004',          # بدل اتصال (جوال)
    'Other': '3201010011',           # بدلات اخرى
    # Commissions paid inside a payslip clear the accrual the commission
    # run booked (2107010001), they are not a second expense.
    'KSW_COMMISSIONS': '2107010001',
}
DEPARTMENT_ACCOUNTS = {
    'Compound': '3204010001',          # رواتب واجور (كامب الجبيل)
    "Tariq's Branch": '3201010007',    # اجور ورواتب سائقين الايسوزو
    "Disouky's Branch": '3201010007',
    # «رواتب السعوديين M»: not a nationality; the IST staff and Non-K.
    'HR-IST': '3201010010',
    'ALHAYAT-IST': '3205010005',      # اجور ورواتب الاداريين الحياة H1 (paid by Al-Hayat)
    'Non-K': '3201010010',
    'Equipment Rental Department': '3206010001',   # رواتب واجور فرع نقليات الكوثر
}
# Deductions BAS credits to the employee's own account, on a line of their own.
PERSONAL_ACCOUNT_RULES = ('GOSI', 'VACATION_GOSI')


def post_init_hook(env):
    Account = env['account.account'].sudo()
    for company in env['res.company'].search([]):
        if not Account.search_count([('company_ids', 'in', company.id), ('code', '=', '3201010001')]):
            continue      # not on BAS's chart: nothing to seed
        env['ksw.payroll.ledger']._journal('SAL', company)
        env['ksw.payroll.ledger']._journal('COMM', company)

        def account(code):
            return Account.search([('company_ids', 'in', company.id), ('code', '=', code)], limit=1)
        for code, acc in RULE_ACCOUNTS.items():
            target = account(acc)
            rules = env['hr.salary.rule'].sudo().with_context(active_test=False).search(
                [('code', '=', code), ('x_ledger_account_id', '=', False)])
            if target and rules:
                rules.write({'x_ledger_account_id': target.id})
        Dept = env['hr.department'].sudo().with_context(active_test=False, lang='en_US')
        for name, acc in DEPARTMENT_ACCOUNTS.items():
            target = account(acc)
            depts = Dept.search([('name', '=', name), ('x_wage_account_id', '=', False)])
            if target and depts:
                depts.write({'x_wage_account_id': target.id})
        seed_hayat(env, company)
        env['hr.salary.rule'].sudo().with_context(active_test=False).search(
            [('code', 'in', PERSONAL_ACCOUNT_RULES)]).write({'x_ledger_to_personal_account': True})
        _logger.info('KSW_payroll_ledger: journals and BAS salary mapping seeded for %s', company.name)


# Al-Hayat's own staff and paying accounts (BAS voucher 26009232, Sep 2026).
HAYAT_DEPARTMENTS = ('ALHAYAT',)
HAYAT_BANK_PREFIX = 'Hayat'
HAYAT_EMPLOYEE_ACCOUNTS = {
    '50070': '3205010005',   # SUAD ALMAQHAWI: admin, اجور ورواتب الاداريين الحياة H1
    '50047': '3205010026',   # RENJITH: ice factory, العاملين التلج H1
}


def seed_hayat(env, company):
    """Only where nothing is set: after this it is configuration."""
    env = env(context=dict(env.context, active_test=False, lang='en_US'))
    env['hr.department'].sudo().search([
        ('name', 'in', HAYAT_DEPARTMENTS), ('x_ledger_entity', '=', False),
    ]).write({'x_ledger_entity': 'hayat'})
    # Filtered here, not searched: res.partner.bank searches acc_number on its
    # sanitised form, which for Hayat WPS is the IBAN, not the label.
    env['res.partner.bank'].sudo().search([
        ('partner_id', '=', company.partner_id.id), ('x_ledger_entity', '=', False),
    ]).filtered(lambda b: (b.acc_number or '').lower().startswith(HAYAT_BANK_PREFIX.lower())).write(
        {'x_ledger_entity': 'hayat'})
    Account = env['account.account'].sudo()
    for number, code in HAYAT_EMPLOYEE_ACCOUNTS.items():
        account = Account.search([('company_ids', 'in', company.id), ('code', '=', code)], limit=1)
        employee = env['hr.employee'].sudo().search([('x_employee_no', '=', number)])
        if account and len(employee) == 1 and not employee.x_wage_account_id:
            employee.x_wage_account_id = account

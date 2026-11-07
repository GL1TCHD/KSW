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
}


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
        _logger.info('KSW_payroll_ledger: journals and BAS salary mapping seeded for %s', company.name)

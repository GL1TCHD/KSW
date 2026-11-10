"""Keep what the replaced fields say, before the update drops their columns.

19.0.1.6.0 moves account determination into Payroll Posting tables. The old
fields (department/employee wage account, separate salary tick, allowances
account, ledger entity, own-account flag, the account parameters) are read
here by SQL and kept in a scratch table for the post-migrate step.
"""
import json


def _columns(cr, table):
    cr.execute("SELECT column_name FROM information_schema.columns WHERE table_name = %s", (table,))
    return {r[0] for r in cr.fetchall()}


def migrate(cr, version):
    legacy = {'rule_accounts': {}, 'fixed_rules': {}, 'dept_accounts': {}, 'dept_hayat': [],
              'bank_hayat': [], 'emp_accounts': {}, 'emp_separate': [], 'emp_allowance': {},
              'rule_personal': [], 'params': {}}
    rule_cols = _columns(cr, 'hr_salary_rule')
    if 'x_ledger_account_id' in rule_cols:
        cr.execute("""SELECT id, x_ledger_account_id, x_bas_posting FROM hr_salary_rule
                       WHERE x_ledger_account_id IS NOT NULL""")
        for rid, acc, posting in cr.fetchall():
            legacy['rule_accounts' if posting == 'expense' else 'fixed_rules'][rid] = acc
    if 'x_ledger_to_personal_account' in rule_cols:
        cr.execute("SELECT id FROM hr_salary_rule WHERE x_ledger_to_personal_account")
        legacy['rule_personal'] = [r[0] for r in cr.fetchall()]
    dept_cols = _columns(cr, 'hr_department')
    if 'x_wage_account_id' in dept_cols:
        cr.execute("SELECT id, x_wage_account_id FROM hr_department WHERE x_wage_account_id IS NOT NULL")
        legacy['dept_accounts'] = dict(cr.fetchall())
    if 'x_ledger_entity' in dept_cols:
        cr.execute("SELECT id FROM hr_department WHERE x_ledger_entity = 'hayat'")
        legacy['dept_hayat'] = [r[0] for r in cr.fetchall()]
    if 'x_ledger_entity' in _columns(cr, 'res_partner_bank'):
        cr.execute("SELECT id FROM res_partner_bank WHERE x_ledger_entity = 'hayat'")
        legacy['bank_hayat'] = [r[0] for r in cr.fetchall()]
    emp_cols = _columns(cr, 'hr_employee')
    if 'x_wage_account_id' in emp_cols:
        cr.execute("SELECT id, x_wage_account_id FROM hr_employee WHERE x_wage_account_id IS NOT NULL")
        legacy['emp_accounts'] = dict(cr.fetchall())
    if 'x_separate_salary_account' in emp_cols:
        cr.execute("SELECT id FROM hr_employee WHERE x_separate_salary_account")
        legacy['emp_separate'] = [r[0] for r in cr.fetchall()]
    if 'x_allowance_account_id' in emp_cols:
        cr.execute("SELECT id, x_allowance_account_id FROM hr_employee WHERE x_allowance_account_id IS NOT NULL")
        legacy['emp_allowance'] = dict(cr.fetchall())
    keys = {'main': 'wage_account', 'separate': 'saudi_wage_account', 'payable': 'payable_account',
            'hayat_wage': 'hayat_wage_account', 'hayat_payable': 'hayat_payable_account',
            'hayat_current': 'hayat_current_account', 'kawther_current': 'kawther_current_account'}
    for key, param in keys.items():
        cr.execute("SELECT value FROM ir_config_parameter WHERE key = %s", ('ksw_payroll_ledger.' + param,))
        row = cr.fetchone()
        if row and row[0]:
            legacy['params'][key] = row[0]
    cr.execute("CREATE TABLE IF NOT EXISTS ksw_payroll_ledger_legacy_1_6 (data jsonb)")
    cr.execute("DELETE FROM ksw_payroll_ledger_legacy_1_6")
    cr.execute("INSERT INTO ksw_payroll_ledger_legacy_1_6 VALUES (%s)", (json.dumps(legacy),))

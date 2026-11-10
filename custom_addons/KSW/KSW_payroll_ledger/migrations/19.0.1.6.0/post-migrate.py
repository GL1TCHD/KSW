"""Payroll Posting tables built from what the replaced fields said.

The entries must not move: every wage account in use becomes a posting group
reproducing the old behaviour (see hooks.build_posting_config). The scratch
table from pre-migrate is dropped afterwards.
"""
import logging

from odoo import SUPERUSER_ID, api

from odoo.addons.KSW_payroll_ledger.hooks import build_posting_config

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    cr.execute("SELECT to_regclass('ksw_payroll_ledger_legacy_1_6')")
    if not cr.fetchone()[0]:
        return
    cr.execute("SELECT data FROM ksw_payroll_ledger_legacy_1_6 LIMIT 1")
    row = cr.fetchone()
    data = row[0] if row else {}
    legacy = {
        'rule_accounts': {int(k): v for k, v in data.get('rule_accounts', {}).items()},
        'fixed_rules': {int(k): v for k, v in data.get('fixed_rules', {}).items()},
        'dept_accounts': {int(k): v for k, v in data.get('dept_accounts', {}).items()},
        'dept_hayat': set(data.get('dept_hayat', [])),
        'bank_hayat': set(data.get('bank_hayat', [])),
        'emp_accounts': {int(k): v for k, v in data.get('emp_accounts', {}).items()},
        'emp_separate': set(data.get('emp_separate', [])),
        'emp_allowance': {int(k): v for k, v in data.get('emp_allowance', {}).items()},
        'rule_personal': set(data.get('rule_personal', [])),
        'params': data.get('params', {}),
    }
    env = api.Environment(cr, SUPERUSER_ID, {})
    for company in env['res.company'].search([]):
        if env['account.account'].search_count([('company_ids', 'in', company.id), ('code', '=', '3201010001')]):
            build_posting_config(env, company, legacy)
    cr.execute("DROP TABLE ksw_payroll_ledger_legacy_1_6")
    _logger.info('KSW_payroll_ledger: legacy accounts → posting configuration: %s departments, '
                 '%s employees, %s ticked separate, %s allowance exceptions',
                 len(legacy['dept_accounts']), len(legacy['emp_accounts']),
                 len(legacy['emp_separate']), len(legacy['emp_allowance']))

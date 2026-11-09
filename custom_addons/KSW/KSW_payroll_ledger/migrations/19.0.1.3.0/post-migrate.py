"""Al-Hayat's salaries in its own accounts (Nov 2026).

Until now a payslip paid from Hayat Raj Cards / Hayat WPS was booked like a
Kawther one (3201xxxx, payable 2107010004). BAS keys it in H1 (voucher
26009232): Al-Hayat's staff to 3205010005/019/026 and payable 2107010008;
a Kawther employee Al-Hayat pays through the two current accounts.

ALHAYAT-IST was given 3201010010 by 19.0.1.2.0; that is the account when
Al-Kawther pays them, which the code now knows by itself. Their own is the
H1 admin account.
"""
import logging

from odoo import SUPERUSER_ID, api

from odoo.addons.KSW_payroll_ledger.hooks import seed_hayat

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {'active_test': False, 'lang': 'en_US'})
    Account = env['account.account']
    for company in env['res.company'].search([]):
        separate = Account.search([('company_ids', 'in', company.id), ('code', '=', '3201010010')], limit=1)
        admin = Account.search([('company_ids', 'in', company.id), ('code', '=', '3205010005')], limit=1)
        if not (separate and admin):
            continue
        env['hr.department'].search([
            ('name', '=', 'ALHAYAT-IST'), ('x_wage_account_id', '=', separate.id),
        ]).write({'x_wage_account_id': admin.id})
        seed_hayat(env, company)
    _logger.info(
        'KSW_payroll_ledger: Al-Hayat entity on %s; Hayat bank accounts: %s',
        ', '.join(env['hr.department'].search([('x_ledger_entity', '=', 'hayat')]).mapped('name')),
        ', '.join(env['res.partner.bank'].search([('x_ledger_entity', '=', 'hayat')]).mapped('acc_number')))

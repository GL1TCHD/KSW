"""3201010010 is not the Saudis' account (Nov 2026).

19.0.1.0.0 sent every Saudi national's wage to «مصروفات رواتب السعوديين M».
BAS keeps that account for the IST staff and for a list of employees it books
apart for its own reasons; Saudis elsewhere are on 3201010001 like everyone
else. The list below is read off BAS, every 3201010010 line Jun to Sep 2026
(vouchers 2600xxxx «رواتب البطاقات» / «الحسابات الجارية»). After this it is
HR's: the Separate Salary Account box on the employee.

Entries already posted are left alone: they are reversed and reposted by hand.
"""
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)

# The departments BAS books whole: IST staff and Non-K.
DEPARTMENTS = ('HR-IST', 'ALHAYAT-IST', 'Non-K')

# (employee no., name): matched on the number, else the exact name.
EMPLOYEES = [
    ('1333', 'MUHAMMAD RIAZ MUHAMMAD SIDDIQUE'),     # محمد رياض صديق
    (None, 'IFTIKHAR HUSSAIN SHAH ZAHOOR HUSSAIN SHAH'),  # افتخار حسين
    ('1472', 'MOHAMMAD M M MILTON'),                 # محمد ميلتون
    ('50053', 'MAHMOUD ISMAIEL WADIE ISMAIEL'),      # محمود اسماعيل
    ('1129', 'DHANESH KUMAR MADAKKA'),               # دانيش كومار
    ('1471', 'SHA ALAM BEPARY'),                     # شاه علم بياري
    ('1502', 'ANOOP KUNUMMAL'),                      # انوب
    ('1501', 'NIJESH KAVUMKULATH'),                  # نجيش
    ('101', 'MOHAMMED AMIN GHANI ALSHAIBANI'),       # د / محمد امين
    ('420', 'HUSSAIN ABD RAB AL REDHA AL SHAKHS'),   # حسين الشخص
    ('842', 'AHMED NAZMI ELSAYED HUSSEIN'),          # احمد نظمي
    ('203', 'Tariq Abu Al-Hassan'),                  # طارق ابو الحسن
    ('991', 'AHMED SAID RAMADAN ELDEIB'),            # احمد الديب
    ('1150', 'MOHAMED IBRAHIM HASSAN IBRAHIM'),      # محمد ابراهيم
    ('1476', 'THARIQ AZEEZ MOHAMED'),                # طارق عزيز
]


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {'active_test': False, 'lang': 'en_US'})
    Account = env['account.account']
    for company in env['res.company'].search([]):
        account = Account.search([('company_ids', 'in', company.id), ('code', '=', '3201010010')], limit=1)
        if not account:
            continue
        depts = env['hr.department'].search([
            ('name', 'in', DEPARTMENTS), ('company_id', 'in', (company.id, False)),
            ('x_wage_account_id', '=', False)])
        depts.write({'x_wage_account_id': account.id})
        _logger.info('KSW_payroll_ledger: 3201010010 set on %s', ', '.join(depts.mapped('name')) or 'no department')

    Employee = env['hr.employee']
    ticked, missing = Employee, []
    for number, name in EMPLOYEES:
        emp = Employee.search([('x_employee_no', '=', number)]) if number else Employee
        if len(emp) != 1:
            emp = Employee.search([('name', '=ilike', name.strip())])
        if len(emp) != 1:
            missing.append('%s (%s)' % (name, number or 'no number'))
            continue
        ticked |= emp
    ticked.write({'x_separate_salary_account': True})
    _logger.info('KSW_payroll_ledger: Separate Salary Account ticked on %s employees', len(ticked))
    if missing:
        _logger.warning('KSW_payroll_ledger: not found, tick by hand: %s', '; '.join(missing))

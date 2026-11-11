{
    'name': 'KSW - Payroll Analyst',
    'category': 'Human Resources/Payroll',
    'version': '19.0.1.0.0',
    'author': 'Mohammed Albadr',
    'summary': 'Pre-payment checks on payslip batches and a plain-language '
               'explanation on every payslip',
    'description': """
Payroll Analyst
===============
Read-only assistant for the payroll officer:

* **Check Payroll** on a payslip batch runs deterministic checks drawn from
  past KSW payroll incidents (negative / zero NET, a month paid twice,
  deductions that the payslip and the installment disagree on, attendance
  changed after the worked days were built, unconfirmed sheets, large NET
  swings with the lines that caused them, missing employees, missing bank
  accounts) and lists every finding with its evidence and the money at
  stake. Findings can be marked reviewed; the mark survives a re-check as
  long as the finding is unchanged.
* **Explanation** tab on the payslip: what was earned, what was deducted
  and why, and what changed since the previous month, built only from the
  payslip's stored lines.

Nothing here recomputes, confirms, cancels or edits a payslip.
    """,
    'license': 'LGPL-3',
    'depends': [
        'KSW_payroll',
        'KSW_deduction',
        'KSW_attendance_sheet',
    ],
    'data': [
        'security/ir.model.access.csv',
        'data/ir_cron.xml',
        'views/payroll_finding_views.xml',
        'views/hr_payslip_run_views.xml',
        'views/hr_payslip_views.xml',
    ],
    'installable': True,
    'auto_install': False,
    'application': False,
}

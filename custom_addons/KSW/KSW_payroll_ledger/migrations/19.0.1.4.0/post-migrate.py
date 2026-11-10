"""Employee and department on the SAL / COMM lines already booked (Nov 2026).

New entries are stamped when they are booked. The ones before carry the
employee only in the line label, so he is read back from it: against the
payslips / commission month the entry belongs to, or against every employee
for an entry nothing links to (a reversal). The department is his current
one; the entries are all from the last few weeks.
"""
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {'active_test': False})
    Line = env['account.move.line']
    Employee = env['hr.employee']
    moves = env['account.move'].search([('journal_id.code', 'in', ('SAL', 'COMM'))])
    linked = {}
    for slip in env['hr.payslip'].search([('x_ledger_move_id', 'in', moves.ids)]):
        linked.setdefault(slip.x_ledger_move_id.id, Employee)
        linked[slip.x_ledger_move_id.id] |= slip.employee_id
    for run in env['ksw.pay.run'].search([('x_ledger_move_id', 'in', moves.ids)]):
        linked.setdefault(run.x_ledger_move_id.id, Employee)
        linked[run.x_ledger_move_id.id] |= run.line_ids.employee_id
    everyone = Line._ksw_employee_by_label(Employee.search([]))
    stamped = unmatched = 0
    for move in moves:
        match = Line._ksw_employee_by_label(linked[move.id]) if move.id in linked else everyone
        by_employee = {}
        for line in move.line_ids.filtered(lambda l: not l.x_employee_id):
            employee = match(line.name)
            if employee:
                by_employee.setdefault(employee, Line)
                by_employee[employee] |= line
            else:
                unmatched += 1
        for employee, lines in by_employee.items():
            lines.write(Line._ksw_employee_vals(employee))
            stamped += len(lines)
    _logger.info('KSW_payroll_ledger: employee stamped on %s lines of %s entries, %s left blank',
                 stamped, len(moves), unmatched)

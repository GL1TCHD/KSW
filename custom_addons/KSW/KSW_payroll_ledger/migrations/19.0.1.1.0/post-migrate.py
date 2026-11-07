"""The ledger entry moves from the batch to each payslip (Nov 2026).

A batch is confirmed one paying account at a time, so it is booked one
confirmation at a time and each payslip carries its own entry. The batches
posted under 19.0.1.0.0 hand their entry to their confirmed payslips; the old
column is read by SQL, the field is gone from the code.
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    cr.execute("""SELECT 1 FROM information_schema.columns
                   WHERE table_name = 'hr_payslip_run' AND column_name = 'x_ledger_move_id'""")
    if not cr.fetchone():
        return
    cr.execute("""
        UPDATE hr_payslip s SET x_ledger_move_id = r.x_ledger_move_id
          FROM hr_payslip_run r
         WHERE s.payslip_run_id = r.id AND r.x_ledger_move_id IS NOT NULL
           AND s.state = 'done' AND s.x_ledger_move_id IS NULL
    """)
    _logger.info('KSW_payroll_ledger: %s payslips took their batch ledger entry.', cr.rowcount)

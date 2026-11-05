"""Vacation / EOS settlements already transferred by hand.

Off-cycle bank files now carry vacation, EOS and unpaid-leave settlement
payslips. Every one confirmed before this release was transferred by hand
(accounting, Nov 2026), so they are marked as such and never offered to a
bank file. The exception is SULEMAN UMMED KHAN's SLIP/14955 (KSWCO payslip
20054, 12,471 SAR, confirmed 2026-11-03), which had not been transferred:
it stays pending for the first file.

The cut-off is the moment accounting confirmed the list (start of
2026-11-05, Riyadh). A settlement confirmed after it and before this
upgrade is left pending; check it with accounting at deploy time.

Idempotent: only touches rows not yet in a file or already marked.
"""
import logging

_logger = logging.getLogger(__name__)

CUTOFF_UTC = '2026-11-04 21:00:00'
STILL_UNPAID = ('SLIP/14955',)


def migrate(cr, version):
    if not version:
        return
    cr.execute("""
        UPDATE hr_payslip
           SET x_paid_outside_bank_file = TRUE
         WHERE state = 'done'
           AND x_leave_id IS NOT NULL
           AND COALESCE(x_is_revision, FALSE) = FALSE
           AND COALESCE(x_is_vacation_preview, FALSE) = FALSE
           AND x_net_wage > 0
           AND x_offcycle_bank_file_id IS NULL
           AND COALESCE(x_paid_outside_bank_file, FALSE) = FALSE
           AND create_date < %s
           AND COALESCE(number, '') NOT IN %s
     RETURNING number
    """, (CUTOFF_UTC, STILL_UNPAID))
    marked = [r[0] for r in cr.fetchall()]
    _logger.info(
        'Off-cycle bank file: %s vacation/EOS settlement(s) marked as '
        'transferred by hand: %s', len(marked), ', '.join(sorted(
            n or '?' for n in marked)))

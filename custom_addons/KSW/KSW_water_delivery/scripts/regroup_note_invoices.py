# Rewrite invoices made one line per delivery note (before 19.0.1.26.0) into
# one sales line per product, rate and revenue account. Total, VAT and
# payments never move: account.move._water_regroup_note_lines refuses an
# invoice whose total would, and re-matches its payments.
#   .venv/bin/python odoo-bin shell -c <conf> --no-http < this file
# Idempotent and resumable: commits per invoice; a regrouped invoice has no
# section left, so a second run skips it.
import subprocess
import time
from collections import Counter

# While the native replay runs, rewrite only during its notes phase (the run
# script plus its branch-worker subshells). A month close matches collections
# to open invoices, so wait while exactly the main run script is alive.
RUN = '^/bin/bash custom_addons/KSW/KSW_bas_gl_import/scripts/native_replay_run.sh'


def wait_while_closing():
    while True:
        n = len(subprocess.run(['pgrep', '-f', RUN], capture_output=True, text=True).stdout.split())
        if n != 1:
            return
        print('REGROUP paused: replay is closing a month', flush=True)
        time.sleep(120)

Move = env['account.move']
ids = Move.search([('move_type', '=', 'out_invoice'),
                   ('invoice_line_ids.x_water_section', '=', True)], order='id').ids
print('REGROUP candidates', len(ids), flush=True)
done, failed, start = 0, Counter(), time.time()
for i, inv_id in enumerate(ids, 1):
    wait_while_closing()
    inv = Move.browse(inv_id)
    try:
        inv.with_user(inv.create_uid).sudo()._water_regroup_note_lines()
        env.cr.commit()
        done += 1
    except Exception as e:
        env.cr.rollback()
        failed[repr(e)[:150]] += 1
        print('REGROUP FAIL', inv_id, repr(e)[:300], flush=True)
    if i % 50 == 0:
        print('REGROUP', i, '/', len(ids), 'done', done, 'failed', sum(failed.values()),
              '%.0fs' % (time.time() - start), flush=True)
print('REGROUP FINISHED done', done, 'failed', sum(failed.values()), dict(failed.most_common(10)), flush=True)

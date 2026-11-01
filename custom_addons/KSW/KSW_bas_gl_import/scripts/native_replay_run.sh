#!/bin/bash
# Full native replay, month by month: branches in parallel for the notes,
# then the month's invoices / collections / entries in one process.
# Usage: native_replay_run.sh 2026-02 2026-09   (resumable: done documents are skipped)
set -u
cd "$(dirname "$0")/../../../.."
CONF=KSW_bas_native.conf
LOG=${REPLAY_LOG:-$HOME/native_replay_logs}; mkdir -p "$LOG"
SCRIPT=custom_addons/KSW/KSW_bas_gl_import/scripts/native_replay.py
BRANCH_GROUPS=("180" "120" "172" "185 145 190" "170 140 171 160 135 152 175")
shell() { .venv/bin/python odoo-bin shell -c $CONF --no-http --log-level=warn; }
month_end() { date -d "$1-01 +1 month" +%Y-%m-%d; }
m=$1
while [[ "$m" < "$2" || "$m" == "$2" ]]; do
  from="$m-01"; to=$(month_end "$m"); pids=()
  for g in "${BRANCH_GROUPS[@]}"; do
    ( printf 'exec(open("%s").read())\nR = Replay(env); R.load_bas_users()\nfor b in "%s".split():\n    print("NOTES", b, "%s", R.notes(b, "%s", "%s"), flush=True)\nR.stop()\n' \
        "$SCRIPT" "$g" "$m" "$from" "$to" | shell > "$LOG/notes_${m}_${g// /_}.log" 2>&1 ) &
    pids+=($!)
  done
  fail=0; for p in "${pids[@]}"; do wait $p || fail=1; done
  # Exit status only: Odoo logs tracebacks for failures it handles itself.
  if [[ $fail == 1 ]] || [[ $(cat "$LOG"/notes_"$m"_*.log | grep -c "^NOTES") -lt 13 ]]; then
    echo "ABORT $m: a notes worker failed, month not closed"; exit 1
  fi
  printf 'exec(open("%s").read())\nR = Replay(env); R.load_bas_users()\nprint("CLOSE", "%s", R.close_month("%s", "%s"), flush=True)\nR.stop()\n' \
      "$SCRIPT" "$m" "$from" "$to" | shell > "$LOG/close_${m}.log" 2>&1
  grep -h -E "^(NOTES|CLOSE)|Traceback" "$LOG"/*_"$m"*.log
  m=$(date -d "$m-01 +1 month" +%Y-%m)
done

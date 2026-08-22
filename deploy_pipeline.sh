#!/bin/bash
# One-shot deploy runner for the data-pipeline upgrade.
#
#   DATABASE_URL="postgresql://..." ./deploy_pipeline.sh
#
# Exists because pasting the command block into zsh broke on the comment
# lines ("zsh: unknown file attribute: i") and kept running later steps
# after earlier ones failed. This script stops at the first failure, so a
# down database can't cascade into five more stack traces.
#
# BEFORE RUNNING (in this order):
#   1. In Railway: grow the Postgres volume — the 2026-08-22 run filled it
#      and crashed the database into recovery. Wait until the DB accepts
#      connections again.
#   2. In Railway: pause/disable the nightly enrichment cron until this
#      completes. Two writers at once is what compounded the disk problem.
#   3. git pull, then re-run this script.
set -euo pipefail

if [ -z "${DATABASE_URL:-}" ]; then
    echo "Set DATABASE_URL first:  export DATABASE_URL=\"postgresql://...\"" >&2
    exit 1
fi

say() { printf '\n\033[1m== %s ==\033[0m\n' "$*"; }

say "0/7 Waiting for the database to accept connections"
until python - <<'PY'
import os, sys, psycopg2
try:
    psycopg2.connect(os.environ['DATABASE_URL'], connect_timeout=10).close()
except Exception as e:
    print(f"   not ready: {e}")
    sys.exit(1)
PY
do
    sleep 15
done
echo "   database is up"

say "1/7 Schema migration (idempotent)"
python migrate_add_intel_signals.py

say "2/7 Dataset verification (no writes)"
python verify_datasets.py 2>&1 | tee verify_output.txt

say "3/7 Retroactive repair — disk-light resets, dry run then apply"
python fix_retroactive_data.py
python fix_retroactive_data.py --apply

say "4/7 ACRIS refetch under corrected roles (rewrites names, parties, lenders)"
ACRIS_FORCE_REFRESH=1 python step3_enrich_from_acris_parallel.py

say "5/7 Tax liens"
python step4_enrich_from_tax_liens.py

say "6/7 Secretary of State (uses DATABASE_URL directly now)"
python step5_enrich_from_sos.py

say "7/7 New signals"
python step6_enrich_signals.py

say "Done"
echo "Optional follow-ups:"
echo "  python fix_retroactive_data.py --apply --backfill-new-fields   (new PLUTO/HPD columns)"
echo "  re-enable the nightly cron in Railway"
echo "  rotate the Postgres password in Railway (it was shared in chat)"

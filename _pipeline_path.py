"""Import this first in any repo-root script that uses the shared data
modules (socrata_client, step2-step5, ny_sos_lookup) — they live in
dashboard_html/ so the dashboard service can deploy self-contained."""
import os
import sys

_DASH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'dashboard_html')
if _DASH not in sys.path:
    sys.path.insert(0, _DASH)

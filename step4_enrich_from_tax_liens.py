#!/usr/bin/env python3
"""Launcher: the canonical module lives in dashboard_html/ so the dashboard
service (Railway root = dashboard_html) can import it too. This file keeps
the original root path working for the pipeline and cron services."""
import os
import runpy
import sys

_DASH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'dashboard_html')
if _DASH not in sys.path:
    sys.path.insert(0, _DASH)

runpy.run_path(os.path.join(_DASH, 'step4_enrich_from_tax_liens.py'), run_name='__main__')

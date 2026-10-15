# Mobile UI verification

The fixture server renders the real Jinja templates and serves the real CSS/JS,
with synthetic API responses. It does not import the production app, load `.env`,
or connect to PostgreSQL. Use only on localhost; this is not an alternate deployment.

```sh
uv run --with flask python dashboard_html/tests/mobile_preview.py
playwright-cli -s=mobile open http://127.0.0.1:5099/properties
playwright-cli -s=mobile run-code --filename=dashboard_html/tests/mobile_browser_checks.js
playwright-cli -s=mobile run-code --filename=dashboard_html/tests/design_consistency_checks.js
playwright-cli -s=mobile run-code --filename=dashboard_html/tests/workflow_error_checks.js
```

The browser checks cover page overflow at 320, 375, 768, and 1440 pixels, filter
drawer opening/closing and value preservation through resizing, map disclosure,
permit tabs, CRM sheet focus/background behavior, search filters, short landscape
navigation, dark appearance, and reduced motion. The fixture covers populated
property/permit/participant/buyer/deal lists and profiles, plus CRM forms and empty
states. Financial and enrichment actions should not be verified against live data
as part of this UI test.

The contractor directory fixture has distinct sample roles and work types. Search
by company/license, filter by role or probable GC, or set a minimum permit count.
Search for an unmatched name to check the empty state; `preview:error` simulates
a failed request for checking the retry state. All these records are synthetic.

Existing regression checks:

```sh
python3 -m unittest dashboard_html.test_navigation
node properties_navigation_tests.js
node building_profile_permit_tests.js
```

For release QA, also check a physical iPhone/Android device with the keyboard
open, long production records, and authenticated flows. Browser viewport tests do
not simulate the OS keyboard, actual safe-area insets, or production services.

The design suite checks the shared palette and shell on 21 page types in light
and dark mode, including account recovery, entity research, administration and CRM.
It also checks the generated permit cards in dark mode. The workflow error suite
checks failed requests and retries, out-of-order search responses, permit sort
wiring, and contact unlock cancellation/failure/success with synthetic billing.
All suites fail when an assertion fails. The preview reloads edited templates.

Database-backed account, CRM, prospecting and permit controls can be checked with
an explicitly disposable localhost PostgreSQL instance:

```sh
PROSPECTING_TEST_DATABASE_URL=postgresql://127.0.0.1:55439/postgres \
  PYTHON_DOTENV_DISABLED=1 DATABASE_URL=postgresql://127.0.0.1:1/unused \
  uv run --with-requirements dashboard_html/requirements.txt \
  python -m unittest discover -s dashboard_html -p '*tests.py'
```

The new CRM and permit suites each use a temporary schema and refuse remote
database hosts. They test actual SQL and route round trips, including CRUD,
follow-ups, exports, record privacy, all permit sort choices, score filtering
before pagination, future-date exclusion and contractor pagination totals.

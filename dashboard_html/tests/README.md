# Mobile UI verification

The fixture server renders the real Jinja templates and serves the real CSS/JS,
with synthetic API responses. It does not import the production app, load `.env`,
or connect to PostgreSQL. Use only on localhost; this is not an alternate deployment.

```sh
uv run --with flask python dashboard_html/tests/mobile_preview.py
playwright-cli -s=mobile open http://127.0.0.1:5099/properties
playwright-cli -s=mobile run-code --filename=dashboard_html/tests/mobile_browser_checks.js
```

The browser checks cover page overflow at 320, 375, 768, and 1440 pixels, filter
drawer opening/closing and value preservation through resizing, map disclosure,
permit tabs, CRM sheet focus/background behavior, search filters, short landscape
navigation, dark appearance, and reduced motion. The fixture covers populated
property/permit/participant/buyer/deal lists and profiles, plus CRM forms and empty
states. Financial and enrichment actions should not be verified against live data
as part of this UI test.

Existing regression checks:

```sh
python3 -m unittest dashboard_html.test_navigation
node properties_navigation_tests.js
node building_profile_permit_tests.js
```

For release QA, also check a physical iPhone/Android device with the keyboard
open, long production records, and authenticated flows. Browser viewport tests do
not simulate the OS keyboard, actual safe-area insets, or production services.

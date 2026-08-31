"""Regression checks for public page routing and shared navigation."""

import ast
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent
APP_SOURCE = (ROOT / "app.py").read_text()
TEMPLATES = ROOT / "templates"
PUBLIC_TEMPLATES = {
    "home.html",
    "construction.html",
    "properties.html",
    "contractors.html",
    "repeat_buyers.html",
    "sales_alerts.html",
    "external_signals.html",
    "search_results.html",
    "permit_detail.html",
    "building_profile.html",
    "contractor_profile.html",
    "admin_activity.html",
    "admin_team.html",
}
PRIMARY_DESTINATIONS = {
    "/",
    "/permits",
    "/properties",
    "/contractors",
    "/buyers",
    "/alerts",
    "/signals",
}


class NavigationTests(unittest.TestCase):
    def test_every_rendered_template_exists(self):
        tree = ast.parse(APP_SOURCE)
        rendered = {
            node.args[0].value
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "render_template"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        }
        missing = sorted(name for name in rendered if not (TEMPLATES / name).is_file())
        self.assertEqual([], missing, f"Routes reference missing templates: {missing}")

    def test_primary_navigation_destinations_are_live_routes(self):
        routes = set(re.findall(r"@app\.route\(['\"]([^'\"]+)", APP_SOURCE))
        self.assertTrue(PRIMARY_DESTINATIONS.issubset(routes))

    def test_public_pages_use_shared_navigation(self):
        for name in PUBLIC_TEMPLATES:
            source = (TEMPLATES / name).read_text()
            uses_shared_nav = (
                "_site_nav.html" in source or '{% extends "base.html" %}' in source
            )
            self.assertTrue(uses_shared_nav, f"{name} does not use shared navigation")
            self.assertNotIn('<nav class="top-nav">', source, f"{name} has legacy navigation")

    def test_removed_workflow_and_dead_routes_stay_removed(self):
        removed = (
            "/old-dashboard",
            "/investments",
            "/analytics",
            "/watchlists",
            "/api/watchlists",
            "/api/watchlist-digest",
            "/api/crm",
        )
        for route in removed:
            self.assertNotIn(f"@app.route('{route}", APP_SOURCE)

        public_markup = "\n".join((TEMPLATES / name).read_text() for name in PUBLIC_TEMPLATES)
        for phrase in ("Push to CRM", "Export to CRM", "Watch account", "Watch project"):
            self.assertNotIn(phrase, public_markup)

    def test_shared_nav_has_one_link_per_primary_destination(self):
        nav = (TEMPLATES / "_site_nav.html").read_text()
        for destination in PRIMARY_DESTINATIONS:
            self.assertEqual(1, nav.count(f"'{destination}'"), destination)


if __name__ == "__main__":
    unittest.main()

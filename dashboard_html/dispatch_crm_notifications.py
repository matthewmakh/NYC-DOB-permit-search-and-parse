#!/usr/bin/env python3
"""Railway cron entry point for CRM due reminders and Web Push delivery."""

import json

from crm_service import dispatch_due_notifications, init_crm_tables


if __name__ == '__main__':
    init_crm_tables()
    print(json.dumps(dispatch_due_notifications(), sort_keys=True), flush=True)

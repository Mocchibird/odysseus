"""
task_scheduler_fork.py — fork-additive housekeeping defaults.

Keeps the fork additive (see additive-fork-policy) so the upstream
HOUSEKEEPING_DEFAULTS literal in src/task_scheduler.py stays byte-identical and
merges cleanly. The scheduler merges these keys into HOUSEKEEPING_DEFAULTS right
after the upstream literal is defined.
"""
from __future__ import annotations

# Built-in housekeeping tasks added by the fork, keyed by action. Shape mirrors
# the upstream HOUSEKEEPING_DEFAULTS entries (schedule "cron" uses
# cron_expression). "tidy_pings" prunes delivered/expired pings daily at 03:00.
FORK_HOUSEKEEPING = {
    "tidy_pings":           {"name": "Pings Tidy",               "schedule": "cron",  "scheduled_time": None,    "cron_expression": "0 3 * * *", "legacy_names": []},
}

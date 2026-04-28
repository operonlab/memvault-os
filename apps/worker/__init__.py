"""apps.worker — background scheduler for memvault-os.

Runs cron-style jobs (dream consolidation, sleeptime reflection, interest
snapshots) and keeps event-driven reactive pipes (slow thinker, capture →
sleeptime trigger) wired up. Started via `python -m apps.worker.main`.
"""

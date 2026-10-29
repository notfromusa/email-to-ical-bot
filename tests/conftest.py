"""
Pytest configuration and shared fixtures for iCal Bot tests.
"""

import pytest
from workload_monitor import get_workload_monitor


@pytest.fixture(autouse=True)
def reset_workload_monitor_state():
    """Ensure clean workload monitor cooldown state for every test."""
    monitor = get_workload_monitor()
    monitor._cooldown_until = 0.0
    monitor._cooldown_reason = ""
    monitor._cpu_high_count = 0
    monitor._prev_cpu_total = 0
    monitor._prev_cpu_idle = 0
    yield
    monitor._cooldown_until = 0.0
    monitor._cooldown_reason = ""
    monitor._cpu_high_count = 0
    monitor._prev_cpu_total = 0
    monitor._prev_cpu_idle = 0

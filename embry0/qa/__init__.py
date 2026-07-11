"""QA support modules that span workflows, API, and execution layers.

Currently exposes:
  - QAEventBus — in-process pub/sub for run-state changes (Phase 5C).
"""

from embry0.qa.event_bus import QAEventBus

__all__ = ["QAEventBus"]

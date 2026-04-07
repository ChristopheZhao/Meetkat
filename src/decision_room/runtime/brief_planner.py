"""Compatibility shim.

Requirement planning now lives in ``decision_room.orchestration.brief_planner``.
This module re-exports the orchestration-owned symbols temporarily while the
runtime package is being reduced to harness responsibilities only.
"""

from decision_room.orchestration.brief_planner import *  # noqa: F401,F403

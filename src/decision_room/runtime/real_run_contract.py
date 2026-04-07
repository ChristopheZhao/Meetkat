"""Compatibility shim.

Real-run prompt/contract helpers now live in ``decision_room.orchestration``.
This module re-exports them temporarily while the runtime package is being
thinned to harness responsibilities only.
"""

from decision_room.orchestration.real_run_contract import *  # noqa: F401,F403

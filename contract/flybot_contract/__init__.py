"""FlyBot message contract in code, shared by the brain, the simulator and tools.

The source of truth is docs/message-contract.md and docs/schemas/; this package reads them.
"""
from .clock import GAP_CAP_MS, GAP_MS, RobotClock
from .contract import (CONTRACT_MD, REPO, SCHEMA_DIR, TOPIC_SCHEMA, Contract, ContractError, Outbox,
                       offline_status)
from .streams import SEQ_MOD, StreamTracker

__all__ = ["Contract", "ContractError", "Outbox", "offline_status", "TOPIC_SCHEMA", "SCHEMA_DIR",
           "CONTRACT_MD", "REPO", "StreamTracker", "SEQ_MOD", "RobotClock", "GAP_MS", "GAP_CAP_MS"]

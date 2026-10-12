from __future__ import annotations

from alan.agent_catalogue_helpers import ResolverGroup
from alan.agent_catalogue_standalone_background import GROUPS as _BACKGROUND_GROUPS
from alan.agent_catalogue_standalone_chats import GROUPS as _CHAT_GROUPS
from alan.agent_catalogue_standalone_driver_loops import GROUPS as _DRIVER_LOOP_GROUPS
from alan.agent_catalogue_standalone_evals import GROUPS as _EVAL_GROUPS
from alan.agent_catalogue_standalone_legacy import GROUPS as _LEGACY_GROUPS
from alan.agent_catalogue_standalone_operator import GROUPS as _OPERATOR_GROUPS

GROUPS: tuple[ResolverGroup, ...] = (
    *_CHAT_GROUPS,
    *_BACKGROUND_GROUPS,
    *_EVAL_GROUPS,
    *_OPERATOR_GROUPS,
    *_DRIVER_LOOP_GROUPS,
    *_LEGACY_GROUPS,
)

__all__ = ["GROUPS"]

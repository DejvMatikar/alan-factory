from __future__ import annotations

from tests import agent_catalogue_parity as parity
from tests import (
    agent_catalogue_parity_drivers_standalone,
    agent_catalogue_parity_drivers_teams_impl,
    agent_catalogue_parity_drivers_teams_orchestrated,
    agent_catalogue_parity_drivers_teams_static,
)

_DRIVER_MODULES = (
    agent_catalogue_parity_drivers_standalone,
    agent_catalogue_parity_drivers_teams_impl,
    agent_catalogue_parity_drivers_teams_orchestrated,
    agent_catalogue_parity_drivers_teams_static,
)


def register_all_drivers() -> None:
    if parity.DRIVERS:
        return
    for module in _DRIVER_MODULES:
        module.register()

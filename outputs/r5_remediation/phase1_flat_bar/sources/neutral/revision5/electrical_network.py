"""
Revision-5 minimal electrical network.

Purpose:
- explicit generator/unit breaker per R5 turbine bay
- explicit grid-intertie breaker
- selective unit isolation
- grid islanding without automatic generator trips
- optional ANSI-86 lockout
- no power-flow simulation

Relay coordination decides WHEN a breaker must operate.
This module decides WHICH breaker operates.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Dict, Iterable


class BreakerPosition(str, Enum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"


class PlantElectricalMode(str, Enum):
    GRID_CONNECTED = "GRID_CONNECTED"
    ISLANDED = "ISLANDED"


@dataclass(frozen=True)
class ElectricalNode:
    node_id: str
    node_type: str


@dataclass
class CircuitBreaker:
    breaker_id: str
    from_node: str
    to_node: str

    position: BreakerPosition = (
        BreakerPosition.CLOSED
    )

    lockout_86: bool = False
    trip_reason: str | None = None
    trip_source: str | None = None

    def trip(
        self,
        *,
        reason: str,
        source: str,
        lockout: bool = False,
    ) -> None:
        self.position = BreakerPosition.OPEN
        self.trip_reason = str(reason)
        self.trip_source = str(source)

        if lockout:
            self.lockout_86 = True

    def open(
        self,
        *,
        reason: str,
        source: str,
    ) -> None:
        self.trip(
            reason=reason,
            source=source,
            lockout=False,
        )

    def close(self) -> None:
        if self.lockout_86:
            raise RuntimeError(
                f"{self.breaker_id} cannot close: "
                "ANSI-86 lockout active"
            )

        self.position = BreakerPosition.CLOSED
        self.trip_reason = None
        self.trip_source = None

    def reset_86(self) -> None:
        self.lockout_86 = False


class PlantElectricalNetwork:
    """
    Minimal five-unit electrical network.

             GRID
               |
        GRID_INTERTIE_CB
               |
           PLANT_BUS
        /   /   |   \\   \\
      CB  CB   CB   CB   CB
      |   |    |    |    |
     GTG GTG CSTG CSTG BPSTG

    Opening one unit breaker does not affect the others.

    Opening GRID_INTERTIE_CB changes plant mode to ISLANDED
    but does not open any generator breaker.
    """

    GRID_NODE = "GRID"
    PLANT_BUS = "PLANT_BUS"
    GRID_BREAKER = "GRID_INTERTIE_CB"

    def __init__(
        self,
        bay_ids: Iterable[str],
    ) -> None:

        bay_ids = tuple(bay_ids)

        if not bay_ids:
            raise ValueError(
                "at least one bay is required"
            )

        self.bay_ids = bay_ids

        self.nodes: Dict[
            str,
            ElectricalNode,
        ] = {
            self.GRID_NODE: ElectricalNode(
                self.GRID_NODE,
                "GRID",
            ),
            self.PLANT_BUS: ElectricalNode(
                self.PLANT_BUS,
                "BUS",
            ),
        }

        self.breakers: Dict[
            str,
            CircuitBreaker,
        ] = {
            self.GRID_BREAKER: CircuitBreaker(
                breaker_id=self.GRID_BREAKER,
                from_node=self.GRID_NODE,
                to_node=self.PLANT_BUS,
            )
        }

        for bay_id in bay_ids:
            self.nodes[bay_id] = ElectricalNode(
                bay_id,
                "GENERATOR",
            )

            breaker_id = (
                self.unit_breaker_id(
                    bay_id
                )
            )

            self.breakers[
                breaker_id
            ] = CircuitBreaker(
                breaker_id=breaker_id,
                from_node=bay_id,
                to_node=self.PLANT_BUS,
            )

    @staticmethod
    def unit_breaker_id(
        bay_id: str,
    ) -> str:
        return f"{bay_id}_GEN_CB"

    @property
    def mode(
        self,
    ) -> PlantElectricalMode:

        grid_cb = self.breakers[
            self.GRID_BREAKER
        ]

        if (
            grid_cb.position
            == BreakerPosition.CLOSED
        ):
            return (
                PlantElectricalMode
                .GRID_CONNECTED
            )

        return PlantElectricalMode.ISLANDED

    @property
    def grid_connected(self) -> bool:
        return (
            self.mode
            == PlantElectricalMode.GRID_CONNECTED
        )

    def unit_breaker(
        self,
        bay_id: str,
    ) -> CircuitBreaker:

        if bay_id not in self.bay_ids:
            raise KeyError(
                f"unknown bay: {bay_id}"
            )

        return self.breakers[
            self.unit_breaker_id(
                bay_id
            )
        ]

    def unit_available(
        self,
        bay_id: str,
    ) -> bool:

        return (
            self.unit_breaker(
                bay_id
            ).position
            == BreakerPosition.CLOSED
        )

    def trip_unit(
        self,
        bay_id: str,
        *,
        reason: str,
        source: str,
        lockout: bool = False,
    ) -> None:
        """
        Selective unit trip.

        Absolutely no other generator breaker and no grid breaker
        is touched.
        """
        self.unit_breaker(
            bay_id
        ).trip(
            reason=reason,
            source=source,
            lockout=lockout,
        )

    def reset_unit_lockout(
        self,
        bay_id: str,
    ) -> None:

        self.unit_breaker(
            bay_id
        ).reset_86()

    def close_unit_breaker(
        self,
        bay_id: str,
    ) -> None:

        self.unit_breaker(
            bay_id
        ).close()

    def open_grid_intertie(
        self,
        *,
        reason: str,
        source: str,
    ) -> None:
        """
        Grid separation / islanding.

        Generator breakers are deliberately untouched.
        """
        self.breakers[
            self.GRID_BREAKER
        ].open(
            reason=reason,
            source=source,
        )

    def close_grid_intertie(
        self,
    ) -> None:

        self.breakers[
            self.GRID_BREAKER
        ].close()

    def online_units(
        self,
    ) -> tuple[str, ...]:

        return tuple(
            bay_id
            for bay_id in self.bay_ids
            if self.unit_available(
                bay_id
            )
        )

    def snapshot(self) -> dict:
        return {
            "mode": self.mode.value,
            "grid_connected": (
                self.grid_connected
            ),
            "online_units": (
                self.online_units()
            ),
            "breakers": {
                breaker_id: {
                    "position": (
                        breaker.position.value
                    ),
                    "lockout_86": (
                        breaker.lockout_86
                    ),
                    "trip_reason": (
                        breaker.trip_reason
                    ),
                    "trip_source": (
                        breaker.trip_source
                    ),
                    "from_node": (
                        breaker.from_node
                    ),
                    "to_node": (
                        breaker.to_node
                    ),
                }
                for breaker_id, breaker
                in self.breakers.items()
            },
        }

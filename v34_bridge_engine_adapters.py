"""Phase 3.6 — engine adapters.

New module. Pure integration glue: the frozen engine and the frozen
in-file placeholder store it was originally tested against
(`institutional_engine_v34_p02_multipos_candidate.TradingEngineV34P02`,
`v34_p02_broker_adapter.KiteBrokerAdapterMultiPos`) expect two call
shapes that this project's own DURABLE stores (Phase 3.1, 3.2) don't
directly implement - traced precisely, not guessed:

1. `TradingEngineV34P02.__init__` calls `self.store.load(self.clock.
   now().date())` - a store whose `load()` takes a `date` argument and
   ALWAYS returns a `BotState` (never `None`; the in-memory test double,
   `InMemoryStore` in test_v34_p02_multipos_engine.py, ignores the date
   argument entirely and always has a state pre-seeded). `v34_bridge_
   botstate_store.BotStateStore.load()` takes no argument and returns
   `Optional[BotState]` (`None` on a genuine first boot) - by design,
   since a durable store shouldn't invent business policy about what a
   "fresh" BotState looks like. `EngineBotStateStoreAdapter` bridges
   this: on a missing file, it constructs a fresh, default-status
   (STARTUP) `BotState` for the given date - the ONLY new "state"
   this module invents, and it is exactly what `BotState`'s own
   dataclass defaults already say a fresh state looks like
   (`status: EngineStatus = EngineStatus.STARTUP`), not a new policy.

2. `KiteBrokerAdapterMultiPos.place_order()` reads `self.authorizer_
   store.registry` as a plain ATTRIBUTE (not a method call) to build a
   `PortfolioRiskSnapshot`, and `authorize_and_persist()` calls `store.
   save(new_registry)` on that same object. The frozen in-file
   `v34_p02_broker_adapter.AuthorizerRegistryStore` placeholder is
   exactly this shape (`self.registry`, `.save()` that also updates
   `self.registry`) but is purely in-memory - nothing survives a
   restart. `v34_bridge_authorizer_registry_store.AuthorizerRegistryStore`
   (Phase 3.1) is durable but exposes `.load()`/`.save()` only, no
   `.registry` attribute. `EngineAuthorizerRegistryAdapter` bridges this:
   `.registry` is an in-memory mirror kept in sync by `.save()`, which
   PERSISTS FIRST (via the durable store) and only updates the in-memory
   mirror after that succeeds - never the other way around. This is not
   a style preference: if `.save()` updated the mirror before persisting,
   and the durable write then failed, `place_order()`'s caller (which
   propagates the failure uncaught - `authorize_and_persist()`'s own
   docstring: "if this raises, the caller never sees ALLOW") would still
   be looking at an in-memory registry object that already reflects the
   supposedly-failed reservation, exactly the "memory ahead of disk"
   condition this whole project's stores are built to prevent.
"""

from __future__ import annotations

from datetime import date as date_type
from typing import Optional

from v34_bridge_authorizer_registry_store import AuthorizerRegistryStore as DurableAuthorizerRegistryStore
from v34_bridge_botstate_store import BotStateStore
from v34_p02_authorizer import AuthorizerRegistry
from v34_p02_state import BotState


class EngineBotStateStoreAdapter:
    """Satisfies `TradingEngineV34P02`'s own `store.load(date)`/`store.
    save(state)` contract, backed by the durable `BotStateStore`."""

    def __init__(self, durable_store: BotStateStore):
        self.durable_store = durable_store
        self.last_loaded_state: Optional[BotState] = None  # diagnostic only - see load()

    def load(self, today: Optional[date_type] = None) -> BotState:
        state = self.durable_store.load()
        if state is None:
            trading_day = str(today) if today is not None else None
            if trading_day is None:
                raise RuntimeError(
                    "EngineBotStateStoreAdapter.load(): no persisted BotState exists and no date was "
                    "supplied to construct a fresh one - this should never happen via TradingEngineV34P02's "
                    "own __init__, which always passes clock.now().date()."
                )
            state = BotState(trading_day=trading_day)  # defaults: status=STARTUP, no active_trades
        # An independent snapshot, not an alias - BotState is a mutable
        # dataclass and the caller (the engine itself) mutates the
        # returned object in place as its own self.state. Round-tripping
        # through to_dict()/from_dict() (the frozen dataclass's own
        # serialization) is what keeps this a true "what was on disk at
        # load time" diagnostic, not something that silently drifts
        # alongside whatever the engine does to its state afterward -
        # see module docstring's step-9 note on audit reconstructability.
        self.last_loaded_state = BotState.from_dict(state.to_dict())
        return state

    def save(self, state: BotState) -> None:
        self.durable_store.save(state)


class EngineAuthorizerRegistryAdapter:
    """Satisfies `KiteBrokerAdapterMultiPos`'s own `authorizer_store.
    registry` attribute + `.save()` contract, backed by the durable
    `AuthorizerRegistryStore` (Phase 3.1). `.registry` is an in-memory
    mirror; `.save()` persists first, mirrors second - see module
    docstring for why that order is load-bearing, not incidental."""

    def __init__(self, durable_store: DurableAuthorizerRegistryStore):
        self.durable_store = durable_store
        loaded = durable_store.load()
        self.registry: AuthorizerRegistry = loaded if loaded is not None else AuthorizerRegistry()

    def load(self) -> AuthorizerRegistry:
        return self.registry

    def save(self, registry: AuthorizerRegistry) -> None:
        self.durable_store.save(registry)  # persist first - if this raises, self.registry is untouched
        self.registry = registry

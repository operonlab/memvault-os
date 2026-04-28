"""NgRx-style Action + Reducer contracts.

Cannibalized from pystorex (JonesHong/pystorex) — adapted to Workshop:
- No immutables.Map (use plain dict + frozen dataclass)
- No RxPy (use Workshop's EventBus)
- Actions map to existing Event(type, data) format

Usage:
    from src.shared.actions import Action, create_action, create_reducer, on

    # Define actions
    WalletCreated = create_action("finance.wallet.created")
    TransactionCreated = create_action("finance.transaction.created")

    # Define reducer (pure function)
    finance_reducer = create_reducer(
        {"wallets": {}, "total_balance": 0},
        on(WalletCreated, lambda state, action: {
            **state,
            "wallets": {**state["wallets"], action.payload["id"]: action.payload},
        }),
    )
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# ── Action ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Action[P]:
    """Immutable action — describes a state change intent.

    Maps to EventBus Event: type=action.type, data=action.payload.
    """

    type: str
    payload: P | None = field(default=None)

    def __repr__(self) -> str:
        if self.payload is None:
            return f"Action({self.type!r})"
        return f"Action({self.type!r}, {self.payload!r})"


# ── Action Creator ───────────────────────────────────────────────────────


class ActionCreator:
    """Factory for creating typed Actions — preserves .type for matching."""

    def __init__(self, action_type: str):
        self.type = action_type

    def __call__(self, payload: Any = None, **kwargs) -> Action:
        if kwargs and payload is None:
            return Action(type=self.type, payload=kwargs)
        return Action(type=self.type, payload=payload)

    def __repr__(self) -> str:
        return f"ActionCreator({self.type!r})"


def create_action(action_type: str) -> ActionCreator:
    """Create a typed action creator.

    Usage:
        WalletCreated = create_action("finance.wallet.created")
        action = WalletCreated(id="w1", name="Main")
        # → Action(type="finance.wallet.created", payload={"id": "w1", "name": "Main"})
    """
    return ActionCreator(action_type)


# ── Reducer ──────────────────────────────────────────────────────────────

ReducerFn = Any  # Callable[[S, Action], S] — with .initial_state metadata


def on(action_creator_or_type: ActionCreator | str, handler):
    """Map an action type to a handler function.

    Returns a dict entry for create_reducer.
    """
    if isinstance(action_creator_or_type, str):
        action_type = action_creator_or_type
    else:
        action_type = action_creator_or_type.type
    return {action_type: handler}


def create_reducer[S](initial_state: S, *handlers) -> ReducerFn:
    """Create a pure reducer function from action→handler mappings.

    State is stored as immutables.Map for structural sharing.
    Handlers receive Map and should return Map (use s.set() or to_immutable()).

    Usage:
        reducer = create_reducer(
            {"count": 0},
            on(Increment, lambda s, a: s.set("count", s["count"] + 1)),
            on(Reset, lambda s, a: to_immutable({"count": 0})),
        )
    """
    from src.shared.immutable_utils import to_immutable

    immutable_initial = to_immutable(initial_state)
    action_handlers: dict[str, Any] = defaultdict(lambda: lambda state, _: state)

    for handler in handlers:
        if isinstance(handler, dict):
            action_handlers.update(handler)
        elif isinstance(handler, tuple) and len(handler) == 2:
            action_handlers[handler[0]] = handler[1]

    def reducer(state=None, action=None):
        if state is None:
            state = immutable_initial
        if action is None:
            return state
        result = action_handlers[action.type](state, action)
        # Ensure result is always Map
        if result is state:
            return state
        from immutables import Map

        if isinstance(result, Map):
            return result
        return to_immutable(result)

    reducer.initial_state = immutable_initial  # type: ignore[attr-defined]
    reducer.handlers = dict(action_handlers)  # type: ignore[attr-defined]
    return reducer

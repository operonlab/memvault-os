"""Workshop Reactive Protocol — 七概念統一合約。

受 RxJS 六核心概念 + DataFlow compile() 啟發，加上 Workshop 原創的 CompletionSource。

七概念：
  1. Observable  — 可訂閱的異步數據流
  2. Observer    — 接收數據的回呼三件組
  3. Subscription — 執行控制句柄（取消、清理）
  4. Operator    — 純函數式數據變換（最大公因數）
  5. Subject     — 多播 EventEmitter（Observable + Observer）
  6. Scheduler   — 集中式並發調度策略
  7. CompletionSource — exactly-once 完成信號（AsyncSubject 語意）
"""

from __future__ import annotations

import asyncio
import copy
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol, TypeVar, runtime_checkable

logger = logging.getLogger(__name__)

T = TypeVar("T")
T_co = TypeVar("T_co", covariant=True)
T_contra = TypeVar("T_contra", contravariant=True)


# ─── 1. Observable ───────────────────────────────────────────────────────────


@runtime_checkable
class Observable(Protocol[T_co]):
    """可被訂閱的異步數據流。

    RxJS:     invokable collection of future values or events
    Python:   AsyncIterator[T] + subscribe() + pipe()
    Workshop: SSE generators, Redis pubsub, DB query streams, Pipeline 輸出
    """

    def subscribe(self, observer: Observer[T_co]) -> Subscription:
        """開始接收值。回傳 Subscription 用於取消。"""
        ...

    def pipe(self, *operators: Operator) -> Observable:
        """串接多個 Operator 變換此流。"""
        ...


# ─── 2. Observer ─────────────────────────────────────────────────────────────


@runtime_checkable
class Observer(Protocol[T_contra]):
    """接收 Observable 傳遞值的回呼三件組。

    RxJS:     collection of callbacks that knows how to listen to values
    Python:   三個 async method — on_next / on_error / on_complete
    Workshop: EventBus Handler（目前只實作 on_next）
    """

    async def on_next(self, value: T_contra) -> None:
        """處理新到達的值。"""
        ...

    async def on_error(self, error: Exception) -> None:
        """處理上游錯誤。預設行為：log + 繼續。"""
        ...

    async def on_complete(self) -> None:
        """數據流結束信號。"""
        ...


# ─── 3. Subscription ────────────────────────────────────────────────────────


@dataclass
class Subscription:
    """Observable 執行的控制句柄，主要用於取消和清理。

    RxJS:     represents the execution of an Observable, primarily for cancelling
    Python:   包裝 asyncio.Task + teardown chain
    Workshop: 統一 TurnController、AdaptiveRunner._active_tasks、Redis consumer 的取消機制
    """

    _task: asyncio.Task | None = field(default=None, repr=False)
    _teardown: list[Callable] = field(default_factory=list, repr=False)
    _closed: bool = field(default=False, repr=False)

    def add(self, teardown: Callable) -> Subscription:
        """鏈式註冊清理函數。已關閉則立即執行。"""
        if self._closed:
            teardown()
        else:
            self._teardown.append(teardown)
        return self

    def unsubscribe(self) -> None:
        """取消 task + 逆序執行所有 teardown。冪等。"""
        if self._closed:
            return
        self._closed = True
        if self._task and not self._task.done():
            self._task.cancel()
        for fn in reversed(self._teardown):
            try:
                fn()
            except Exception:
                logger.exception("Teardown function failed")
        self._teardown.clear()

    @property
    def closed(self) -> bool:
        return self._closed

    @classmethod
    def empty(cls) -> Subscription:
        """已關閉的空 Subscription。"""
        sub = cls()
        sub._closed = True
        return sub


# ─── FunctionObserver (convenience) ──────────────────────────────────────────


class FunctionObserver:
    """Observer 便捷實作：將裸函數包裝為 Observer Protocol。支援 sync 與 async handler。"""

    def __init__(
        self,
        on_next_fn: Callable,
        *,
        on_error_fn: Callable | None = None,
        name: str = "",
    ) -> None:
        self._on_next_fn = on_next_fn
        self._on_error_fn = on_error_fn
        self._name = name or getattr(on_next_fn, "__name__", "anonymous")

    async def on_next(self, value: Any) -> None:
        result = self._on_next_fn(value)
        if asyncio.iscoroutine(result):
            await result

    async def on_error(self, error: Exception) -> None:
        if self._on_error_fn:
            result = self._on_error_fn(error)
            if asyncio.iscoroutine(result):
                await result
        else:
            logger.exception("FunctionObserver[%s] error: %s", self._name, error)

    async def on_complete(self) -> None:
        pass  # no-op


# ─── 4. Operator ─────────────────────────────────────────────────────────────


@runtime_checkable
class Operator(Protocol):
    """純函數式數據變換——Workshop 所有 pipeline stage 的最大公因數。

    RxJS:     pure functions enabling functional programming style
    Python:   async callable + 聲明式 input_keys / output_keys
    Workshop: Capture strategies, Scoring stages, Nodeflow executors 的統一介面
    DataFlow: OperatorABC.run(storage, input_key, output_key) 的 Workshop 版本
    """

    @property
    def name(self) -> str:
        """Operator 名稱。"""
        ...

    @property
    def input_keys(self) -> tuple[str, ...]:
        """宣告讀取哪些 context keys。"""
        ...

    @property
    def output_keys(self) -> tuple[str, ...]:
        """宣告寫入哪些 context keys。"""
        ...

    async def __call__(self, ctx: dict[str, Any]) -> dict[str, Any]:
        """讀 input_keys → 變換 → 寫 output_keys → 返回更新後的 ctx。"""
        ...


# ─── 5. Subject ──────────────────────────────────────────────────────────────


@runtime_checkable
class Subject(Protocol[T]):
    """同時是 Observable 和 Observer——接收值並多播給所有訂閱者。

    RxJS:     equivalent to EventEmitter, the only way of multicasting
    Python:   subscribe() + next() + error() + complete()
    Workshop: EventBus 是最成熟的 Subject 實作
    """

    def subscribe(self, observer: Observer[T]) -> Subscription:
        """訂閱此 Subject。"""
        ...

    async def next(self, value: T) -> None:
        """發射值給所有訂閱者。"""
        ...

    async def error(self, err: Exception) -> None:
        """通知所有訂閱者發生錯誤。"""
        ...

    async def complete(self) -> None:
        """通知所有訂閱者數據流結束。"""
        ...


# ─── 6. Scheduler ───────────────────────────────────────────────────────────


@runtime_checkable
class Scheduler(Protocol):
    """控制並發的集中調度器——決定「何時」和「如何」執行。

    RxJS:     centralized dispatchers to control concurrency
    Python:   asyncio.Semaphore + 策略注入
    Workshop: MemoryAdaptiveRunner（記憶壓力）、RateLimiter（速率限制）的抽象化
    """

    async def schedule(self, work: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """調度單一工作單元。"""
        ...

    async def schedule_batch(
        self, items: list, processor: Callable
    ) -> list:
        """調度批量工作。結果保持輸入順序。"""
        ...


# ═══════════════════════════════════════════════════════════════════════════
# 組合工具
# ═══════════════════════════════════════════════════════════════════════════


class Pipeline:
    """可驗證的 Operator 組合器——自身也是 Operator（Composite 模式）。

    RxJS pipe() + DataFlow compile() 的結合。
    Pipeline 實作 Operator Protocol，可作為 ConditionalOp 的 then_op/else_op。
    """

    def __init__(self, name: str | None = None) -> None:
        self._ops: list[Operator] = []
        self._name = name

    @property
    def name(self) -> str:
        return self._name or " → ".join(op.name for op in self._ops) or "empty_pipeline"

    @property
    def input_keys(self) -> tuple[str, ...]:
        """精準計算：所有 ops 的 input_keys 減去前面 ops 已提供的 output_keys。"""
        required: set[str] = set()
        provided: set[str] = set()
        for op in self._ops:
            for key in op.input_keys:
                if key not in provided:
                    required.add(key)
            provided |= set(op.output_keys)
        return tuple(sorted(required))

    @property
    def output_keys(self) -> tuple[str, ...]:
        """所有 ops 的 output_keys 聯集。"""
        keys: set[str] = set()
        for op in self._ops:
            keys |= set(op.output_keys)
        return tuple(sorted(keys))

    def pipe(self, *ops: Operator) -> Pipeline:
        """串接 Operator。回傳 self 支援 chaining。"""
        self._ops.extend(ops)
        return self

    def compile(self, initial_keys: set[str] | None = None) -> list[str]:
        """DataFlow 式靜態驗證：檢查 key 依賴鏈完整性。

        Returns:
            missing keys 列表（空 = 驗證通過）。
        """
        available = set(initial_keys) if initial_keys else set()
        missing: list[str] = []

        for op in self._ops:
            for key in op.input_keys:
                if key not in available:
                    missing.append(f"{op.name}: requires '{key}'")
            for key in op.output_keys:
                available.add(key)

        return missing

    async def execute(self, ctx: dict[str, Any]) -> dict[str, Any]:
        """依序執行所有 Operator。向後相容 API。"""
        for op in self._ops:
            ctx = await op(ctx)
        return ctx

    async def __call__(self, ctx: dict[str, Any]) -> dict[str, Any]:
        """Operator Protocol 入口——等同 execute()。"""
        return await self.execute(ctx)

    def __iter__(self):
        return iter(self._ops)

    def __len__(self) -> int:
        return len(self._ops)

    def __repr__(self) -> str:
        names = " → ".join(op.name for op in self._ops)
        return f"Pipeline({names})"


class ConditionalOp:
    """條件分支：predicate=True → then_op, else → else_op 或 passthrough。"""

    def __init__(
        self,
        predicate: Callable[[dict[str, Any]], bool],
        then_op: Operator,
        else_op: Operator | None = None,
        *,
        name: str = "conditional",
        predicate_keys: tuple[str, ...] = (),
    ) -> None:
        self._predicate = predicate
        self._then_op = then_op
        self._else_op = else_op
        self._name = name
        self._predicate_keys = predicate_keys

    @property
    def name(self) -> str:
        return self._name

    @property
    def input_keys(self) -> tuple[str, ...]:
        keys = set(self._predicate_keys)
        keys |= set(self._then_op.input_keys)
        if self._else_op:
            keys |= set(self._else_op.input_keys)
        return tuple(sorted(keys))

    @property
    def output_keys(self) -> tuple[str, ...]:
        keys = set(self._then_op.output_keys)
        if self._else_op:
            keys |= set(self._else_op.output_keys)
        return tuple(sorted(keys))

    async def __call__(self, ctx: dict[str, Any]) -> dict[str, Any]:
        if self._predicate(ctx):
            return await self._then_op(ctx)
        elif self._else_op:
            return await self._else_op(ctx)
        return ctx


class ParallelOp:
    """並行：多個 op 同時處理同一 ctx 的副本，結果 merge。"""

    def __init__(self, *ops: Operator, name: str = "parallel") -> None:
        self._ops = ops
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    @property
    def input_keys(self) -> tuple[str, ...]:
        keys: set[str] = set()
        for op in self._ops:
            keys |= set(op.input_keys)
        return tuple(sorted(keys))

    @property
    def output_keys(self) -> tuple[str, ...]:
        keys: set[str] = set()
        for op in self._ops:
            keys |= set(op.output_keys)
        return tuple(sorted(keys))

    async def __call__(self, ctx: dict[str, Any]) -> dict[str, Any]:
        tasks = [op(copy.deepcopy(ctx)) for op in self._ops]
        results = await asyncio.gather(*tasks)
        merged = dict(ctx)
        for result in results:
            merged.update(result)
        return merged


class ScheduledOp:
    """Scheduler 包裝：將 Operator 的執行委託給 Scheduler。"""

    def __init__(self, op: Operator, scheduler: Scheduler, *, name: str | None = None) -> None:
        self._op = op
        self._scheduler = scheduler
        self._name = name or f"scheduled({op.name})"

    @property
    def name(self) -> str:
        return self._name

    @property
    def input_keys(self) -> tuple[str, ...]:
        return self._op.input_keys

    @property
    def output_keys(self) -> tuple[str, ...]:
        return self._op.output_keys

    async def __call__(self, ctx: dict[str, Any]) -> dict[str, Any]:
        return await self._scheduler.schedule(self._op, ctx)


# ─── 7. CompletionSource ────────────────────────────────────────────────────


@runtime_checkable
class CompletionSource(Protocol[T_co]):
    """Exactly-once 完成信號——AsyncSubject 語意。

    與 Observable 不同：最多發一次值，支援遲到訂閱 replay。
    具體實作：completion.TaskCompletion

    Workshop: 統一 headless subprocess、tmux wait-for、fleet HTTP callback 的完成通知。
    """

    @property
    def task_id(self) -> str:
        """任務識別碼。"""
        ...

    @property
    def status(self) -> str:
        """pending / running / completed / failed / timeout"""
        ...

    def subscribe(self, observer: Observer[T_co]) -> Subscription:
        """訂閱完成信號。已完成時立即 replay。"""
        ...

    async def wait(self, timeout: float | None = None) -> T_co:
        """阻塞直到完成、失敗或超時。"""
        ...

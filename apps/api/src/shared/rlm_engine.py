"""Recursive Language Model (RLM) Engine.

Adapted from arXiv:2512.24601 (Zhang, Kraska, Khattab — MIT).

Treats long prompts as an external environment. The LLM examines, decomposes,
and recursively calls itself over context snippets via a Python REPL sandbox.

LLM backend: ``claude -p`` (headless mode) — no API keys needed.

Usage::

    from src.shared.rlm_engine import RLMEngine, RLMConfig

    engine = RLMEngine(RLMConfig(model="sonnet"))
    result = engine.completion(
        prompt="Find the magic number hidden in this text.",
        context=very_long_text,
    )
    print(result.response)
"""

from __future__ import annotations

import contextlib
import io
import logging
import os
import re
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# ── Configuration ────────────────────────────────────────────────────────────


@dataclass
class RLMConfig:
    """RLM execution configuration."""

    model: str = "sonnet"
    sub_model: str = "haiku"  # model for llm_query() sub-calls
    max_depth: int = 2
    max_iterations: int = 20
    max_timeout_secs: float = 300.0
    max_errors: int = 5
    compaction: bool = True
    compaction_threshold: int = 60_000  # chars in history before compacting
    repl_output_limit: int = 20_000  # max chars per REPL output
    verbose: bool = False
    # LiteLLM / OpenAI-compatible API backend (None = use claude -p)
    api_base: str | None = None  # e.g. "http://localhost:4000/v1"
    api_key: str | None = None  # e.g. "sk-litellm-local-dev"
    # tmux persistent backend (set to tmux window name, e.g. "rlm")
    tmux_target: str | None = None


# ── Result types ─────────────────────────────────────────────────────────────


@dataclass
class RLMUsage:
    total_calls: int = 0
    total_time_secs: float = 0.0


@dataclass
class RLMResult:
    response: str
    usage: RLMUsage = field(default_factory=RLMUsage)
    iterations: int = 0
    depth: int = 0
    execution_time_secs: float = 0.0
    trajectory: list[dict] = field(default_factory=list)
    status: str = "ok"  # ok | timeout | max_iterations | error


# ── LLM Backend ──────────────────────────────────────────────────────────────


def _call_llm(
    prompt: str,
    *,
    system: str = "",
    model: str = "sonnet",
    timeout: float = 120,
    api_base: str | None = None,
    api_key: str | None = None,
    tmux_target: str | None = None,
) -> str:
    """Call LLM via one of three backends.

    Priority: tmux_target > api_base > claude -p.
    """
    if tmux_target:
        full = f"{system}\n\n{prompt}" if system else prompt
        return _call_tmux_persistent(full, target=tmux_target, timeout=timeout)
    if api_base:
        return _call_openai_compat(
            prompt,
            system=system,
            model=model,
            timeout=timeout,
            api_base=api_base,
            api_key=api_key or "",
        )
    return _call_claude_cli(prompt, system=system, model=model, timeout=timeout)


def _call_claude_cli(
    prompt: str,
    *,
    system: str = "",
    model: str = "sonnet",
    timeout: float = 120,
) -> str:
    """Call Claude via headless CLI (claude -p)."""
    cmd = ["claude", "-p", "--model", model, "--output-format", "text"]
    if system:
        cmd.extend(["--system-prompt", system])

    result = subprocess.run(
        cmd,
        input=prompt,
        capture_output=True,
        text=True,
        timeout=int(timeout),
        cwd="/tmp",  # avoid loading project CLAUDE.md
    )
    if result.returncode != 0:
        err = result.stderr[:500] if result.stderr else "unknown error"
        raise RuntimeError(f"claude -p failed (rc={result.returncode}): {err}")
    return result.stdout.strip()


def _call_openai_compat(
    prompt: str,
    *,
    system: str = "",
    model: str = "grok-4-fast",
    timeout: float = 120,
    api_base: str = "http://localhost:4000/v1",
    api_key: str = "",
) -> str:
    """Call LLM via OpenAI-compatible API (LiteLLM, xAI, etc)."""
    import httpx

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    resp = httpx.post(
        f"{api_base}/chat/completions",
        headers=headers,
        json={"model": model, "messages": messages},
        timeout=timeout,
    )
    resp.raise_for_status()
    data = resp.json()
    return data["choices"][0]["message"]["content"].strip()


# ── tmux persistent backend ──────────────────────────────────────────────────

# Timing presets for tmux relay interaction
_SLEEP_PROMPT_DETECT = 0.5  # wait for prompt detection (initial ready-check loop)
_SLEEP_CONTENT_CHANGE = 0.5  # wait for pane content to change after send
_SLEEP_CONTENT_STABLE = 0.8  # poll interval while waiting for content to stabilise
_SLEEP_POST_SEND = 0.4  # settle after send-keys before re-capturing pane
_TIMEOUT_TMUX_CMD = 10  # tmux subprocess timeout (seconds)
_TIMEOUT_READY_CHECK = 5  # paste-buffer / short tmux operation timeout (seconds)
_TMUX_POLL = 0.6  # poll interval for content-stability loop (seconds)

_TMUX_PROMPT_RE = re.compile(r"❯\s*$", re.MULTILINE)
_TMUX_SEND_LIMIT = 512
_tmux_lock = __import__("threading").Lock()


def _tmux_cmd(*args: str, check: bool = False) -> str:
    proc = subprocess.run(
        ["tmux", *args], capture_output=True, text=True, timeout=_TIMEOUT_TMUX_CMD
    )
    if check and proc.returncode != 0:
        raise RuntimeError(f"tmux {args[0]} failed: {proc.stderr.strip()}")
    return proc.stdout.strip()


def _tmux_ensure_window(target: str, model: str = "sonnet") -> bool:
    """Ensure tmux window with a running Claude Code instance."""
    try:
        windows = _tmux_cmd("list-windows", "-F", "#{window_name}")
    except Exception:
        return False

    if target not in windows.split("\n"):
        logger.info("rlm-tmux: creating window %s", target)
        try:
            _tmux_cmd("new-window", "-d", "-n", target, check=True)
            time.sleep(_SLEEP_CONTENT_STABLE)  # wait for shell to initialise in new window
        except Exception:
            return False

    # Check if Claude is already running
    cmd = _tmux_cmd("display-message", "-t", target, "-p", "#{pane_current_command}")
    shells = {"zsh", "bash", "sh", "fish"}
    if cmd.split("/")[-1] in shells or not cmd:
        logger.info("rlm-tmux: starting Claude in window %s", target)
        claude_cmd = f"CLAUDE_VOICE=0 claude --dangerously-skip-permissions --model {model}"
        _tmux_cmd("send-keys", "-t", target, "-l", claude_cmd)
        _tmux_cmd("send-keys", "-t", target, "Enter")
        # Wait for ❯
        deadline = time.time() + 30
        while time.time() < deadline:
            bottom = _tmux_cmd("capture-pane", "-t", target, "-p", "-S", "-5")
            if _TMUX_PROMPT_RE.search(bottom):
                logger.info("rlm-tmux: Claude ready in window %s", target)
                return True
            time.sleep(_SLEEP_CONTENT_STABLE)
        logger.warning("rlm-tmux: Claude failed to start in 30s")
        return False

    return True


def _tmux_is_ready(target: str) -> bool:
    bottom = _tmux_cmd("capture-pane", "-t", target, "-p", "-S", "-5")
    return bool(_TMUX_PROMPT_RE.search(bottom))


def _tmux_capture(target: str, lines: int = 300) -> str:
    return _tmux_cmd("capture-pane", "-t", target, "-p", "-S", str(-lines))


def _tmux_send(target: str, text: str) -> None:
    """Send text to tmux pane. Long text uses load-buffer + paste-buffer."""
    if len(text) > _TMUX_SEND_LIMIT:
        buf = "_rlm_paste"
        subprocess.run(
            ["tmux", "load-buffer", "-b", buf, "-"],
            input=text,
            text=True,
            capture_output=True,
            timeout=_TIMEOUT_READY_CHECK,
            check=True,
        )
        _tmux_cmd("paste-buffer", "-b", buf, "-t", target, "-d", "-p")
    else:
        _tmux_cmd("send-keys", "-t", target, "-l", text)
    _tmux_cmd("send-keys", "-t", target, "Enter")


def _call_tmux_persistent(
    prompt: str,
    *,
    target: str = "rlm",
    timeout: float = 120,
) -> str:
    """Call Claude via persistent tmux instance.

    For long prompts (>2K chars): writes to /tmp file, sends a short read
    instruction. Claude reads the file, processes, writes response to a
    separate file. This avoids pasting huge prompts into tmux.
    """
    import os
    import uuid as _uuid

    with _tmux_lock:
        if not _tmux_ensure_window(target):
            raise RuntimeError(f"Cannot start Claude in tmux window '{target}'")

        if not _tmux_is_ready(target):
            deadline = time.time() + 15
            while time.time() < deadline:
                if _tmux_is_ready(target):
                    break
                time.sleep(_TMUX_POLL)
            else:
                raise RuntimeError("tmux Claude not ready (busy or hung)")

        uid = _uuid.uuid4().hex[:8]
        prompt_file = f"/tmp/rlm-in-{uid}.txt"
        response_file = f"/tmp/rlm-out-{uid}.txt"

        # Always write prompt to file (reliable for any length)
        with open(prompt_file, "w") as f:
            f.write(prompt)

        send_text = (
            f"Read {prompt_file} and respond to it. "
            f"Write ONLY your complete text response to {response_file}. "
            "No explanation, just the response content in the file."
        )

        # Snapshot before
        before = _tmux_capture(target, 100)

        # Send instruction
        _tmux_send(target, send_text)

        # Wait for ❯ (Claude done)
        deadline = time.time() + timeout
        changed = False
        while time.time() < deadline:
            time.sleep(_SLEEP_CONTENT_CHANGE)
            current = _tmux_capture(target, 100)
            if current != before:
                changed = True
                break

        if not changed:
            _cleanup_files(prompt_file, response_file)
            raise RuntimeError("tmux pane never changed — Claude may be stuck")

        stable_count = 0
        last_content = current
        while time.time() < deadline:
            time.sleep(_TMUX_POLL)  # Consider: exponential backoff for long-running responses
            current = _tmux_capture(target, 100)
            if current == last_content:
                stable_count += 1
                if stable_count >= 2 and _tmux_is_ready(target):
                    break
            else:
                stable_count = 0
                last_content = current

        if stable_count < 2:
            _cleanup_files(prompt_file, response_file)
            raise RuntimeError(f"tmux response timed out after {timeout}s")

        # Read response from file
        result = ""
        if os.path.exists(response_file):
            with open(response_file) as f:
                result = f.read().strip()

        _cleanup_files(prompt_file, response_file)

        if result:
            return result

        # Fallback: extract from pane diff
        new_content = current
        for i, (a, b) in enumerate(zip(before, current, strict=False)):
            if a != b:
                new_content = current[i:]
                break
        else:
            if len(current) > len(before):
                new_content = current[len(before) :]

        lines = new_content.rstrip().split("\n")
        while lines and _TMUX_PROMPT_RE.search(lines[-1]):
            lines.pop()

        return "\n".join(lines).strip()


def _cleanup_files(*paths: str) -> None:
    import os

    for p in paths:
        try:
            os.unlink(p)
        except OSError:
            pass


# ── Parsing ──────────────────────────────────────────────────────────────────

_CODE_BLOCK_RE = re.compile(r"```repl\s*\n(.*?)\n```", re.DOTALL)
_FINAL_VAR_RE = re.compile(r"^\s*FINAL_VAR\((.*?)\)", re.MULTILINE | re.DOTALL)
_FINAL_RE = re.compile(r"^\s*FINAL\((.*)\)\s*$", re.MULTILINE | re.DOTALL)


def find_code_blocks(text: str) -> list[str]:
    """Extract ```repl ... ``` code blocks from LLM response."""
    return [m.group(1).strip() for m in _CODE_BLOCK_RE.finditer(text)]


def find_final_answer(text: str, env: REPLSandbox | None = None) -> str | None:
    """Detect FINAL(...) or FINAL_VAR(...) in response text."""
    # FINAL_VAR first
    m = _FINAL_VAR_RE.search(text)
    if m and env is not None:
        var_name = m.group(1).strip().strip("\"'")
        val = env.get_var(var_name)
        if val is not None:
            return str(val)

    # FINAL(...)
    m = _FINAL_RE.search(text)
    if m:
        return m.group(1).strip()

    return None


# ── REPL Sandbox ─────────────────────────────────────────────────────────────

# Safe builtins — blocks eval/exec/compile/input
_SAFE_BUILTINS: dict[str, Any] = {}


def _init_safe_builtins() -> dict[str, Any]:
    """Build safe builtins dict once."""
    import builtins

    allowed = [
        "print",
        "len",
        "str",
        "int",
        "float",
        "list",
        "dict",
        "set",
        "tuple",
        "bool",
        "type",
        "isinstance",
        "issubclass",
        "enumerate",
        "zip",
        "map",
        "filter",
        "sorted",
        "reversed",
        "range",
        "min",
        "max",
        "sum",
        "abs",
        "round",
        "any",
        "all",
        "pow",
        "divmod",
        "chr",
        "ord",
        "hex",
        "bin",
        "oct",
        "repr",
        "format",
        "hash",
        "id",
        "iter",
        "next",
        "slice",
        "callable",
        "hasattr",
        "getattr",
        "setattr",
        "delattr",
        "dir",
        "bytes",
        "bytearray",
        "complex",
        "object",
        "super",
        "property",
        "staticmethod",
        "classmethod",
        # Exceptions
        "Exception",
        "ValueError",
        "TypeError",
        "KeyError",
        "IndexError",
        "AttributeError",
        "RuntimeError",
        "StopIteration",
        "ImportError",
        "FileNotFoundError",
        "OSError",
        "IOError",
        "NotImplementedError",
        "AssertionError",
    ]
    safe = {}
    for name in allowed:
        if hasattr(builtins, name):
            safe[name] = getattr(builtins, name)
    # Explicitly block dangerous builtins
    safe["eval"] = None
    safe["exec"] = None
    safe["compile"] = None
    safe["input"] = None
    safe["globals"] = None
    safe["locals"] = None
    safe["__import__"] = None  # block dynamic import
    safe["True"] = True
    safe["False"] = False
    safe["None"] = None

    # Restricted open: only allow reading /tmp/rlm-* paths
    _real_open = builtins.open

    def _safe_open(file, mode="r", *args, **kwargs):  # type: ignore[no-untyped-def]
        path = str(file)
        if not path.startswith("/tmp/rlm-"):
            raise PermissionError(
                f"open() restricted to /tmp/rlm-* paths in sandbox (got: {path!r})"
            )
        return _real_open(file, mode, *args, **kwargs)

    safe["open"] = _safe_open
    return safe


class REPLSandbox:
    """Sandboxed Python REPL for RLM code execution.

    Provides a restricted execution environment with:
    - Safe builtins (no eval/exec/compile)
    - Protected scaffold functions (llm_query, rlm_query, context, etc.)
    - Variable isolation between iterations
    """

    def __init__(self) -> None:
        global _SAFE_BUILTINS
        if not _SAFE_BUILTINS:
            _SAFE_BUILTINS = _init_safe_builtins()

        self._globals: dict[str, Any] = {"__builtins__": dict(_SAFE_BUILTINS)}
        self._locals: dict[str, Any] = {}
        self._protected: set[str] = set()
        self._final_answer: str | None = None

    def inject(self, name: str, value: Any, *, protected: bool = False) -> None:
        """Inject a variable or function into the REPL namespace."""
        self._locals[name] = value
        if protected:
            self._protected.add(name)
            self._globals["__builtins__"][name] = value

    def execute(self, code: str) -> tuple[str, str]:
        """Execute Python code in the sandbox.

        Returns:
            (stdout, stderr) tuple.
        """
        stdout_buf = io.StringIO()
        stderr_buf = io.StringIO()

        combined = {**self._globals, **self._locals}

        with contextlib.redirect_stdout(stdout_buf), contextlib.redirect_stderr(stderr_buf):
            try:
                exec(code, combined, combined)  # noqa: S102 — sandboxed
            except Exception as e:
                stderr_buf.write(f"{type(e).__name__}: {e}")

        # Update locals with new variables
        for k, v in combined.items():
            if k not in self._globals and not k.startswith("_"):
                self._locals[k] = v

        # Restore protected scaffold (LLM might have overwritten context, etc.)
        for key in self._protected:
            if key in self._locals:
                combined[key] = self._locals[key]

        return stdout_buf.getvalue(), stderr_buf.getvalue()

    def get_var(self, name: str) -> Any:
        """Get a variable from the REPL namespace."""
        return self._locals.get(name)

    def list_vars(self) -> dict[str, str]:
        """List all user variables and their types."""
        return {
            k: type(v).__name__
            for k, v in self._locals.items()
            if not k.startswith("_") and k not in self._protected
        }


# ── Prompt Templates ─────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """\
You are tasked with answering a query using associated context. You can access, \
transform, and analyze this context interactively in a REPL environment that can \
recursively query sub-LLMs.

The REPL environment provides:
1. A `context` variable containing the input data. Check its type and content first.
2. `llm_query(prompt)` — single LLM call (one-shot, ~500K char input). Use for \
   simple extraction, summarization, Q&A over a chunk.
3. `llm_query_batched(prompts)` — concurrent `llm_query` calls, returns List[str].
4. `rlm_query(prompt)` — recursive RLM sub-call. The child gets its own REPL and \
   iterates. Use for multi-step reasoning that needs its own loop.
5. `SHOW_VARS()` — list all variables you have created.
6. `print()` — view REPL output to guide your next step.

**Strategy**: Break problems into digestible pieces. Chunk large contexts, query \
an LLM per chunk, save answers to buffers, then aggregate. Use `llm_query_batched` \
for independent queries — much faster than sequential calls.

When to use llm_query vs rlm_query:
- llm_query: simple extraction, summarization, classification (one-shot)
- rlm_query: complex reasoning, multi-step problem-solving (own iteration loop)

Write Python code in ```repl ... ``` blocks. Code executes in the REPL and you \
see the output. Use variables as buffers to build your final answer.

When done, provide your final answer using one of:
- FINAL(your answer here) — direct answer text
- FINAL_VAR(variable_name) — return an existing variable (create it in a repl block first)

Think step by step, plan, then execute immediately. Do not just describe what \
you will do — actually do it in code."""


def _build_context_metadata(context: str | list[str]) -> str:
    if isinstance(context, list):
        lengths = [len(c) for c in context]
        total = sum(lengths)
        n = len(context)
        preview = str(lengths[:20]) + (f"... [{n - 20} more]" if n > 20 else "")
        return f"Context: list of {n} chunks, total {total:,} chars, lengths: {preview}"
    return f"Context: string, {len(context):,} chars"


def _build_user_prompt(query: str, iteration: int) -> str:
    if iteration == 0:
        return (
            "You have not interacted with the REPL yet. Start by examining the "
            "context variable and planning your approach.\n\n"
            f"Query: {query}\n\n"
            "Your next action:"
        )
    return (
        "The history above shows your previous REPL interactions. "
        "Continue using the REPL to answer the query.\n\n"
        f"Query: {query}\n\n"
        "Your next action:"
    )


# ── Message History Formatting ───────────────────────────────────────────────


def _escape_role_markers(text: str) -> str:
    """Escape role markers to prevent prompt injection in transcript format."""
    return (
        text.replace("[User]", "[User_]")
        .replace("[Assistant]", "[Assistant_]")
        .replace("[Context Info]", "[Context_Info]")
    )


def _format_history_as_transcript(messages: list[dict[str, str]]) -> str:
    """Format message history as a structured transcript for claude -p."""
    parts = []
    for msg in messages:
        role = msg["role"]
        content = msg["content"]
        if role == "assistant":
            # Assistant content is trusted; no escaping needed
            parts.append(f"[Assistant]\n{content}")
        elif role == "user":
            parts.append(f"[User]\n{_escape_role_markers(content)}")
        elif role == "metadata":
            parts.append(f"[Context Info]\n{_escape_role_markers(content)}")
    return "\n\n---\n\n".join(parts)


def _format_execution_result(code: str, stdout: str, stderr: str, *, limit: int = 20_000) -> str:
    """Format REPL execution result for message history."""
    parts = [f"Code executed:\n```python\n{code}\n```\n\nREPL output:"]
    if stdout:
        text = (
            stdout
            if len(stdout) <= limit
            else stdout[:limit] + f"... [{len(stdout) - limit} chars truncated]"
        )
        parts.append(text)
    if stderr:
        parts.append(f"Error: {stderr[:2000]}")
    if not stdout and not stderr:
        parts.append("(no output)")
    return "\n".join(parts)


# ── RLM Engine ───────────────────────────────────────────────────────────────


class RLMEngine:
    """Recursive Language Model inference engine.

    Implements the RLM paradigm: LLM writes Python code in a REPL loop,
    can recursively call sub-LLMs, and terminates by calling FINAL().

    Args:
        config: Engine configuration.
        depth: Current recursion depth (internal use).
    """

    def __init__(self, config: RLMConfig | None = None, *, depth: int = 0) -> None:
        self.config = config or RLMConfig()
        self.depth = depth
        self._usage = RLMUsage()
        self._start_time: float = 0.0
        self._error_count: int = 0

    def _llm(
        self, prompt: str, *, system: str = "", model: str | None = None, timeout: float = 120
    ) -> str:
        """Call LLM with config-level backend settings automatically applied."""
        return _call_llm(
            prompt,
            system=system,
            model=model or self.config.model,
            timeout=timeout,
            api_base=self.config.api_base,
            api_key=self.config.api_key,
            tmux_target=self.config.tmux_target,
        )

    # ── Public API ───────────────────────────────────────────────────────

    def completion(
        self,
        prompt: str,
        context: str | list[str] | None = None,
    ) -> RLMResult:
        """Run RLM inference loop.

        Automatically selects conversational mode for tmux backend
        (leverages session memory, only sends incremental updates).
        """
        self._start_time = time.time()

        if self.depth >= self.config.max_depth:
            return self._fallback_direct(prompt, context)

        # tmux persistent: use conversational mode (stateful)
        if self.config.tmux_target:
            return self._completion_tmux(prompt, context)

        # Stateless backends (claude -p, LiteLLM API): full history each call
        return self._completion_stateless(prompt, context)

    def _completion_tmux(
        self,
        prompt: str,
        context: str | list[str] | None = None,
    ) -> RLMResult:
        """Conversational RLM via tmux persistent Claude.

        Leverages session memory: only the first call sends full context,
        subsequent calls just send REPL execution results.
        """
        import uuid as _uuid

        trajectory: list[dict] = []
        target = self.config.tmux_target
        ctx_file: str | None = None

        try:
            # Setup REPL sandbox (local, same as stateless)
            env = self._setup_environment(context)

            # Ensure tmux window is ready
            if not _tmux_ensure_window(target, self.config.model):
                return self._build_result("[tmux window failed]", trajectory, 0, "error")

            if not _tmux_is_ready(target):
                deadline = time.time() + 20
                while time.time() < deadline:
                    if _tmux_is_ready(target):
                        break
                    time.sleep(_SLEEP_PROMPT_DETECT)
                else:
                    return self._build_result("[tmux Claude not ready]", trajectory, 0, "error")

            # First turn: send system prompt + context + query
            # For large context, write to file and reference it
            first_prompt_parts = [_SYSTEM_PROMPT, ""]
            if context is not None:
                first_prompt_parts.append(_build_context_metadata(context))
                if isinstance(context, str) and len(context) > 5000:
                    ctx_file = f"/tmp/rlm-ctx-{_uuid.uuid4().hex[:8]}.txt"
                    with open(ctx_file, "w") as f:
                        f.write(context)
                    first_prompt_parts.append(
                        f"\nThe full context is saved at {ctx_file}. "
                        "Read it when you need to examine the context."
                    )
                elif isinstance(context, list):
                    ctx_file = f"/tmp/rlm-ctx-{_uuid.uuid4().hex[:8]}.txt"
                    with open(ctx_file, "w") as f:
                        for idx, chunk in enumerate(context):
                            f.write(f"=== CHUNK {idx} ===\n{chunk}\n\n")
                    first_prompt_parts.append(
                        f"\nThe full context ({len(context)} chunks) is saved at {ctx_file}. "
                        "Read it when you need to examine the context."
                    )
                else:
                    first_prompt_parts.append(f"\nContext:\n{context}")
            first_prompt_parts.append(f"\nQuery: {prompt}")
            first_prompt_parts.append(
                "\nStart by examining the context and planning your approach. "
                "Write Python code in ```repl blocks."
            )

            for i in range(self.config.max_iterations):
                elapsed = time.time() - self._start_time
                if elapsed > self.config.max_timeout_secs:
                    return self._build_result("[Timeout exceeded]", trajectory, i, "timeout")

                remaining = self.config.max_timeout_secs - elapsed

                # Determine what to send
                if i == 0:
                    send_text = "\n".join(first_prompt_parts)
                else:
                    # Subsequent turns: just send REPL output
                    send_text = _build_user_prompt(prompt, i)

                # Send to tmux and get response
                try:
                    with _tmux_lock:
                        response = self._tmux_send_and_capture(target, send_text, remaining)
                    self._usage.total_calls += 1
                    self._error_count = 0
                except Exception as e:
                    self._error_count += 1
                    if self.config.verbose:
                        logger.warning("rlm-tmux[iter=%d] error: %s", i, e)
                    if self._error_count >= self.config.max_errors:
                        return self._build_result(f"[tmux error: {e}]", trajectory, i, "error")
                    continue

                if self.config.verbose:
                    logger.info("rlm-tmux[iter=%d] response: %.200s", i, response)

                # Parse and execute code blocks
                code_blocks = find_code_blocks(response)
                exec_results: list[tuple[str, str, str]] = []

                for code in code_blocks:
                    stdout, stderr = env.execute(code)
                    exec_results.append((code, stdout, stderr))

                # Check FINAL_VAR from REPL
                if env._final_answer is not None:
                    trajectory.append({"iteration": i, "action": "FINAL_VAR_repl"})
                    return self._build_result(env._final_answer, trajectory, i + 1)

                # Check FINAL in text
                final = find_final_answer(response, env)
                if final:
                    trajectory.append(
                        {"iteration": i, "action": "FINAL", "response_preview": response[:300]}
                    )
                    return self._build_result(final, trajectory, i + 1)

                # tmux heuristic: if response is short, no code blocks, no FINAL
                # → Claude likely just answered directly (interactive mode style)
                if i > 0 and not code_blocks and len(response) < 500 and response.strip():
                    clean = response.strip().strip('"').strip("'")
                    if clean and "\n" not in clean:
                        trajectory.append({"iteration": i, "action": "direct_answer"})
                        return self._build_result(clean, trajectory, i + 1)

                # Send REPL results back as the next message
                if exec_results:
                    repl_feedback = []
                    for code, stdout, stderr in exec_results:
                        repl_feedback.append(
                            _format_execution_result(
                                code, stdout, stderr, limit=self.config.repl_output_limit
                            )
                        )
                    # Prepend REPL output to next iteration's send_text
                    first_prompt_parts = []  # clear first prompt
                    next_repl = "\n\n".join(repl_feedback)
                    # We'll send this as the "user" message next iteration
                    # by overriding the build_user_prompt with REPL results
                    _build_user_prompt_override = (
                        f"{next_repl}\n\nContinue using the REPL"
                        f" to answer: {prompt}\nYour next action:"
                    )
                    # Send REPL results immediately to the tmux session
                    try:
                        with _tmux_lock:
                            self._tmux_send_and_capture(
                                target, _build_user_prompt_override, remaining
                            )
                        self._usage.total_calls += 1

                        # This response is the NEXT iteration — parse it too
                        # (we handle it in the next loop iteration)
                    except Exception:
                        pass  # will retry in next loop iteration

                trajectory.append(
                    {
                        "iteration": i,
                        "action": "continue",
                        "code_blocks": len(code_blocks),
                        "response_preview": response[:200],
                    }
                )

            return self._build_result(
                "[Max iterations exceeded]",
                trajectory,
                self.config.max_iterations,
                "max_iterations",
            )
        finally:
            if ctx_file and os.path.exists(ctx_file):
                os.unlink(ctx_file)

    def _tmux_send_and_capture(self, target: str, text: str, timeout: float) -> str:
        """Send text to tmux Claude, wait for response, return it."""
        before = _tmux_capture(target, 200)
        _tmux_send(target, text)

        # Wait for content change
        deadline = time.time() + timeout
        while time.time() < deadline:
            time.sleep(_SLEEP_POST_SEND)
            current = _tmux_capture(target, 200)
            if current != before:
                break
        else:
            raise RuntimeError("tmux pane never changed")

        # Wait for stable + ❯
        stable_count = 0
        last = current
        while time.time() < deadline:
            time.sleep(_TMUX_POLL)  # Consider: exponential backoff for long-running responses
            current = _tmux_capture(target, 200)
            if current == last:
                stable_count += 1
                if stable_count >= 2 and _tmux_is_ready(target):
                    break
            else:
                stable_count = 0
                last = current

        if stable_count < 2:
            raise RuntimeError("tmux response timed out")

        # Extract new content
        new_content = current
        for idx, (a, b) in enumerate(zip(before, current, strict=False)):
            if a != b:
                new_content = current[idx:]
                break
        else:
            if len(current) > len(before):
                new_content = current[len(before) :]

        lines = new_content.rstrip().split("\n")
        # Remove trailing ❯ prompt lines
        while lines and _TMUX_PROMPT_RE.search(lines[-1]):
            lines.pop()

        # Extract Claude Code response: lines starting with ⏺ are model output.
        # Also include ```repl blocks and FINAL() if present.
        response_lines = []
        for line in lines:
            stripped = line.lstrip()
            # Claude Code prefixes model output with ⏺
            if stripped.startswith("⏺"):
                response_lines.append(stripped[1:].strip())
            # Pass through repl blocks and FINAL markers
            elif stripped.startswith("```repl") or stripped.startswith("```"):
                response_lines.append(stripped)
            elif stripped.startswith("FINAL(") or stripped.startswith("FINAL_VAR("):
                response_lines.append(stripped)
            # Continuation lines of model output (indented, not UI chrome)
            elif (
                response_lines
                and stripped
                and not any(c in stripped for c in ["🔖", "📁", "⎇", "🤖", "✍️", "⏵"])
            ):
                response_lines.append(stripped)

        return "\n".join(response_lines).strip()

    def _completion_stateless(
        self,
        prompt: str,
        context: str | list[str] | None = None,
    ) -> RLMResult:
        """Stateless RLM (claude -p / LiteLLM API) — sends full history each call."""
        trajectory: list[dict] = []

        # Setup REPL sandbox
        env = self._setup_environment(context)

        # Build initial message history
        messages: list[dict[str, str]] = []
        if context is not None:
            messages.append({"role": "metadata", "content": _build_context_metadata(context)})

        # Main inference loop
        for i in range(self.config.max_iterations):
            # Check timeout
            elapsed = time.time() - self._start_time
            if elapsed > self.config.max_timeout_secs:
                return self._build_result(
                    "[Timeout exceeded]",
                    trajectory,
                    i,
                    status="timeout",
                )

            # Compaction: summarize history if too long
            if self.config.compaction and i > 0:
                history_len = sum(len(m["content"]) for m in messages)
                if history_len > self.config.compaction_threshold:
                    messages = self._compact_history(messages, prompt)

            # Build prompt for this turn
            user_msg = _build_user_prompt(prompt, i)

            # Call LLM
            try:
                full_prompt = _format_history_as_transcript(
                    [*messages, {"role": "user", "content": user_msg}]
                )
                remaining = self.config.max_timeout_secs - (time.time() - self._start_time)
                response = self._llm(
                    full_prompt,
                    system=_SYSTEM_PROMPT,
                    model=self.config.model,
                    timeout=max(remaining, 10),
                )
                self._usage.total_calls += 1
                self._error_count = 0
            except Exception as e:
                self._error_count += 1
                logger.warning(
                    "rlm[depth=%d] LLM call error (attempt %d): %s",
                    self.depth,
                    self._error_count,
                    e,
                )
                if self._error_count >= self.config.max_errors:
                    return self._build_result(
                        f"[Too many errors: {e}]",
                        trajectory,
                        i,
                        status="error",
                    )
                continue

            if self.config.verbose:
                logger.info("rlm[depth=%d][iter=%d] response: %.200s", self.depth, i, response)

            # Parse and execute code blocks FIRST (before checking FINAL)
            # Paper: the LLM may write code + FINAL in same response;
            # code must run so FINAL_VAR can reference computed variables.
            code_blocks = find_code_blocks(response)
            exec_results: list[tuple[str, str, str]] = []

            for code in code_blocks:
                stdout, stderr = env.execute(code)
                exec_results.append((code, stdout, stderr))

                if self.config.verbose:
                    logger.info(
                        "rlm[depth=%d][iter=%d] code: %.100s → out: %.200s",
                        self.depth,
                        i,
                        code,
                        stdout,
                    )

            # Check if FINAL_VAR was called inside REPL code
            if env._final_answer is not None:
                trajectory.append({"iteration": i, "action": "FINAL_VAR_repl"})
                return self._build_result(env._final_answer, trajectory, i + 1)

            # Check for FINAL(...) or FINAL_VAR(...) in response text
            final = find_final_answer(response, env)
            if final:
                trajectory.append(
                    {"iteration": i, "action": "FINAL", "response_preview": response[:300]}
                )
                return self._build_result(final, trajectory, i + 1)

            # Append to message history
            messages.append({"role": "assistant", "content": response})
            for code, stdout, stderr in exec_results:
                messages.append(
                    {
                        "role": "user",
                        "content": _format_execution_result(
                            code, stdout, stderr, limit=self.config.repl_output_limit
                        ),
                    }
                )

            trajectory.append(
                {
                    "iteration": i,
                    "action": "continue",
                    "code_blocks": len(code_blocks),
                    "response_preview": response[:200],
                }
            )

        return self._build_result(
            "[Max iterations exceeded]",
            trajectory,
            self.config.max_iterations,
            status="max_iterations",
        )

    # ── REPL Setup ───────────────────────────────────────────────────────

    def _setup_environment(self, context: str | list[str] | None) -> REPLSandbox:
        """Create and configure REPL sandbox with injected functions."""
        env = REPLSandbox()

        # Inject context
        if context is not None:
            env.inject("context", context, protected=True)

        # Inject LLM query functions
        env.inject("llm_query", self._llm_query, protected=True)
        env.inject("llm_query_batched", self._llm_query_batched, protected=True)
        env.inject("rlm_query", self._rlm_query, protected=True)
        env.inject("SHOW_VARS", env.list_vars, protected=True)

        # Inject FINAL_VAR as a function that sets the final answer
        def _final_var(var_name: str | Any) -> str:
            if isinstance(var_name, str):
                var_name = var_name.strip().strip("\"'")
                val = env.get_var(var_name)
                if val is not None:
                    env._final_answer = str(val)
                    return str(val)
                return f"Error: Variable '{var_name}' not found"
            env._final_answer = str(var_name)
            return str(var_name)

        env.inject("FINAL_VAR", _final_var, protected=True)

        return env

    # ── LLM Query Functions (injected into REPL) ────────────────────────

    def _llm_query(self, prompt: str, model: str | None = None) -> str:
        """Single LLM call (no REPL, no iteration). Injected as llm_query()."""
        self._check_timeout()
        model = model or self.config.sub_model
        try:
            response = self._llm(
                str(prompt),
                model=model,
                timeout=max(self._remaining_time(), 10),
            )
            self._usage.total_calls += 1
            return response
        except Exception as e:
            return f"[llm_query error: {e}]"

    def _llm_query_batched(self, prompts: list[str], model: str | None = None) -> list[str]:
        """Concurrent LLM calls. Injected as llm_query_batched()."""
        self._check_timeout()
        model = model or self.config.sub_model
        results: list[str] = [""] * len(prompts)

        with ThreadPoolExecutor(max_workers=min(len(prompts), 4)) as pool:
            futures = {pool.submit(self._llm_query, p, model): idx for idx, p in enumerate(prompts)}
            for future in as_completed(futures):
                idx = futures[future]
                try:
                    results[idx] = future.result()
                except Exception as e:
                    results[idx] = f"[error: {e}]"

        return results

    def _rlm_query(self, prompt: str, model: str | None = None) -> str:
        """Recursive RLM sub-call. Injected as rlm_query()."""
        self._check_timeout()
        next_depth = self.depth + 1

        if next_depth >= self.config.max_depth:
            # Degrade to simple llm_query
            return self._llm_query(prompt, model)

        child_config = RLMConfig(
            model=model or self.config.model,
            sub_model=self.config.sub_model,
            max_depth=self.config.max_depth,
            max_iterations=self.config.max_iterations,
            max_timeout_secs=max(self._remaining_time() - 5, 10),
            max_errors=self.config.max_errors,
            compaction=self.config.compaction,
            verbose=self.config.verbose,
        )
        child = RLMEngine(child_config, depth=next_depth)
        result = child.completion(prompt=prompt, context=None)

        # Propagate usage
        self._usage.total_calls += result.usage.total_calls

        return result.response

    # ── Compaction ───────────────────────────────────────────────────────

    def _compact_history(
        self,
        messages: list[dict[str, str]],
        original_query: str,
    ) -> list[dict[str, str]]:
        """Summarize conversation history to reduce context size."""
        transcript = _format_history_as_transcript(messages)
        summary_prompt = (
            f"Summarize your progress so far on this task:\n\n"
            f"Original query: {original_query}\n\n"
            f"Conversation history:\n{transcript}\n\n"
            "Include:\n"
            "1. What steps you've completed\n"
            "2. Key intermediate results (with specific values)\n"
            "3. What remains to be done\n"
            "Be concise but preserve all important data."
        )
        try:
            summary = self._llm(summary_prompt, model=self.config.sub_model, timeout=30)
            self._usage.total_calls += 1
        except Exception:
            # If compaction fails, keep first + last few messages
            return messages[:2] + messages[-4:]

        # Reset history with summary
        new_messages = []
        # Keep metadata if present
        if messages and messages[0]["role"] == "metadata":
            new_messages.append(messages[0])
        new_messages.append(
            {
                "role": "user",
                "content": (
                    f"[Conversation compacted]\nProgress summary:\n{summary}"
                    "\n\nContinue from where you left off."
                ),
            }
        )
        logger.info(
            "rlm[depth=%d] compacted history: %d msgs → %d",
            self.depth,
            len(messages),
            len(new_messages),
        )
        return new_messages

    # ── Helpers ──────────────────────────────────────────────────────────

    def _fallback_direct(self, prompt: str, context: str | list[str] | None) -> RLMResult:
        """Direct LLM call without REPL (when max_depth reached)."""
        ctx_str = ""
        if context:
            ctx_str = context if isinstance(context, str) else "\n---\n".join(context)
            ctx_str = f"\n\nContext:\n{ctx_str[:100_000]}"
        full = f"{prompt}{ctx_str}"
        try:
            response = self._llm(full, model=self.config.model, timeout=60)
            self._usage.total_calls += 1
        except Exception as e:
            response = f"[Direct call error: {e}]"
        return RLMResult(
            response=response,
            usage=self._usage,
            depth=self.depth,
            execution_time_secs=time.time() - self._start_time,
            status="ok",
        )

    def _build_result(
        self,
        response: str,
        trajectory: list[dict],
        iterations: int,
        status: str = "ok",
    ) -> RLMResult:
        self._usage.total_time_secs = time.time() - self._start_time
        return RLMResult(
            response=response,
            usage=self._usage,
            iterations=iterations,
            depth=self.depth,
            execution_time_secs=time.time() - self._start_time,
            trajectory=trajectory,
            status=status,
        )

    def _remaining_time(self) -> float:
        return self.config.max_timeout_secs - (time.time() - self._start_time)

    def _check_timeout(self) -> None:
        if time.time() - self._start_time > self.config.max_timeout_secs:
            raise TimeoutError("RLM timeout exceeded")

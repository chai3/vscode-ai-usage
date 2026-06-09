"""
VSCode GitHub Copilot Chat usage report generator CLI
Generates AI_USAGE_REPORT.md (no external dependencies)
"""

import json
import os
from datetime import date, datetime, timedelta, timezone
from typing import Any, NamedTuple, TypedDict
from urllib.parse import unquote

ModelId = str
SessionId = str
WorkspaceId = str
FolderUri = str
JsonObj = dict[str, Any]


OUTPUT_FILE = "AI_USAGE_REPORT.md"
CHARS_PER_THINKING_TOKEN = 5
VSCODE_EDITIONS: list[str] = ["Code", "Code - Insiders", "Code - Exploration"]
TOOL_RESPONSE_KINDS: tuple[str, ...] = (
    "toolInvocationSerialized",
    "progressTaskSerialized",
)
FALLBACK_MODEL_PRICING: ModelId = "gpt-4o-mini"


class _PricingRequired(TypedDict):
    input: float
    output: float


class PricingEntry(_PricingRequired, total=False):
    """Per-model pricing ($/million tokens). `cached` is optional."""

    cached: float


class SessionInfo(TypedDict):
    session_id: SessionId
    workspace_id: WorkspaceId
    folder_path: FolderUri
    title: str
    last_active: datetime
    turns: int
    tools: int
    input: int
    output: int
    thinking: int
    cached: int
    total: int
    models: str
    cost: float
    cost_detail: str
    chat_session_path: str


class PeriodStats(NamedTuple):
    cost: float
    sessions: int
    turns: int


class DateRange(NamedTuple):
    start: date
    end: date


# ---------------------------------------------------------------------------
# Model pricing table (GitHub Copilot AI Credit rates, $/million tokens)
# Fallback key: "default"
#
# Source: copilotPricing blocks in
#   https://github.com/rajbos/ai-engineering-fluency/blob/main/vscode-extension/src/modelPricing.json
#   (last updated 2026-06-03)
# ---------------------------------------------------------------------------
MODEL_PRICING: dict[ModelId, PricingEntry] = {
    "gpt-4o": {"input": 5.0, "output": 20.0, "cached": 2.5},
    "gpt-4o-mini": {"input": 0.15, "output": 0.6, "cached": 0.075},
    "gpt-4.1": {"input": 2.0, "output": 8.0, "cached": 0.5},
    "gpt-4.1-mini": {"input": 0.4, "output": 1.6, "cached": 0.1},
    "gpt-4.1-nano": {"input": 0.1, "output": 0.4, "cached": 0.025},
    "gpt-5": {"input": 1.25, "output": 10.0},
    "gpt-5-mini": {"input": 0.25, "output": 2.0, "cached": 0.025},
    "gpt-5.4": {"input": 2.5, "output": 15.0, "cached": 0.25},
    "gpt-5.4-mini": {"input": 0.75, "output": 4.5, "cached": 0.075},
    "o1": {"input": 15.0, "output": 60.0},
    "o1-mini": {"input": 3.0, "output": 12.0},
    "o3": {"input": 2.0, "output": 8.0},
    "o3-mini": {"input": 1.1, "output": 4.4},
    "o4-mini": {"input": 1.1, "output": 4.4},
    "claude-sonnet-4.5": {"input": 3.0, "output": 15.0, "cached": 0.3},
    "claude-sonnet-4.6": {"input": 3.0, "output": 15.0, "cached": 0.3},
    "claude-opus-4.5": {"input": 5.0, "output": 25.0, "cached": 0.5},
    "claude-haiku-4.5": {"input": 1.0, "output": 5.0, "cached": 0.1},
    "gemini-2.5-pro": {"input": 1.25, "output": 10.0, "cached": 0.125},
    "gemini-2.5-flash": {"input": 0.15, "output": 0.6, "cached": 0.0375},
    "gemini-2.0-flash": {"input": 0.1, "output": 0.4, "cached": 0.025},
    "default": {"input": 0.15, "output": 0.6, "cached": 0.075},
}


def get_workspace_storage_dirs() -> list[str]:
    """Return existing workspaceStorage paths for all installed VS Code editions.

    Checks Windows (%APPDATA%), macOS (~/Library/Application Support),
    and Linux (~/.config) paths without OS detection.
    """
    home = os.path.expanduser("~")
    base_dirs = [
        os.environ.get("APPDATA", ""),                                      # Windows
        os.path.join(home, "Library", "Application Support"),               # macOS
        os.path.join(home, ".config"),                                       # Linux
    ]
    dirs: list[str] = []
    for base in base_dirs:
        if not base:
            continue
        for edition in VSCODE_EDITIONS:
            path = os.path.join(base, edition, "User", "workspaceStorage")
            if os.path.isdir(path):
                dirs.append(path)
    return dirs


def _set_at_path(obj: JsonObj | list[Any], keys: list[str | int], value: Any) -> None:
    """Apply a kind=1 patch: set `value` at the nested `keys` path."""
    for key in keys[:-1]:
        if isinstance(key, int):
            obj = obj[key]  # type: ignore[index]
        else:
            assert isinstance(obj, dict)
            obj = obj.setdefault(key, {})
    last = keys[-1]
    if isinstance(obj, list):
        assert isinstance(last, int)
        while len(obj) <= last:
            obj.append({})
        obj[last] = value
    else:
        assert isinstance(obj, dict)
        obj[last] = value  # type: ignore[index]


def _append_at_path(
    obj: JsonObj | list[Any], keys: list[str | int], value: Any
) -> None:
    """Apply a kind=2 patch: append `value` to the array at `keys` path."""
    for key in keys:
        if isinstance(key, int):
            obj = obj[key]  # type: ignore[index]
        else:
            assert isinstance(obj, dict)
            obj = obj.setdefault(key, [])
    assert isinstance(obj, list)
    if isinstance(value, list):
        obj.extend(value)
    else:
        obj.append(value)


def _apply_delta(state: JsonObj, record: JsonObj) -> None:
    kind: int | None = record.get("kind")
    if kind == 0:
        state.update(record.get("v", {}))
    elif kind == 1:
        keys: list[str | int] | None = record.get("k")
        if keys:
            try:
                _set_at_path(state, keys, record.get("v"))
            except (IndexError, KeyError, TypeError, AssertionError):
                pass
    elif kind == 2:
        keys = record.get("k")
        if keys is not None:
            try:
                _append_at_path(state, keys, record.get("v"))
            except (IndexError, KeyError, TypeError, AssertionError):
                pass


def parse_jsonl(filepath: str) -> JsonObj:
    """Parse a VS Code delta JSONL file and return the reconstructed state."""
    state: JsonObj = {}
    with open(filepath, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                _apply_delta(state, json.loads(line))
            except Exception:
                pass
    return state


def _lookup_pricing(model_id: ModelId) -> PricingEntry:
    """Return pricing for `model_id`, falling back to FALLBACK_MODEL_PRICING with a warning."""
    if model_id in MODEL_PRICING:
        return MODEL_PRICING[model_id]
    for key in MODEL_PRICING:
        if key != "default" and model_id.startswith(key):
            return MODEL_PRICING[key]
    print(
        f"[WARNING] unknown model '{model_id}', falling back to '{FALLBACK_MODEL_PRICING}' pricing"
    )
    return MODEL_PRICING[FALLBACK_MODEL_PRICING]


def _calc_cost(
    prompt_tokens: int,
    output_tokens: int,
    cached_tokens: int,
    model_id: ModelId,
) -> float:
    pricing = _lookup_pricing(model_id)
    uncached = max(0, prompt_tokens - cached_tokens)
    return (
        uncached * pricing["input"]
        + cached_tokens * pricing.get("cached", pricing["input"])
        + output_tokens * pricing["output"]
    ) / 1_000_000


def _strip_copilot_prefix(raw_model_id: str) -> ModelId:
    return raw_model_id.replace("copilot/", "") or "unknown"


def extract_session(
    state: JsonObj,
    workspace_id: WorkspaceId,
    folder_path: FolderUri,
    filepath: str,
) -> SessionInfo | None:
    """Extract a SessionInfo from a reconstructed delta-JSONL state, or None."""
    session_id: SessionId = state.get("sessionId") or workspace_id[:8]
    requests: list[JsonObj] = state.get("requests") or []
    if not requests:
        return None

    # LastActive: latest completedAt or timestamp across all requests (ms)
    last_active_ms: int | None = None
    for req in requests:
        ms: int | None = (req.get("modelState") or {}).get("completedAt") or req.get(
            "timestamp"
        )
        if ms and (last_active_ms is None or ms > last_active_ms):
            last_active_ms = ms
    if last_active_ms is None:
        print(f"[WARNING] session {session_id}: no timestamp found, skipping")
        return None

    last_active = datetime.fromtimestamp(
        last_active_ms / 1000, tz=timezone.utc
    ).astimezone()

    total_input: int = 0
    total_output: int = 0
    total_thinking: int = 0
    total_cached: int = 0
    total_tools: int = 0
    models: list[ModelId] = []
    cost_parts: list[str] = []
    has_tokens = False

    for req_idx, req in enumerate(requests):
        metadata: JsonObj = ((req.get("result") or {}).get("metadata")) or {}
        prompt_tokens = int(metadata.get("promptTokens", 0) or 0)
        output_tokens = int(metadata.get("outputTokens", 0) or 0)
        cached_tokens = int(
            metadata.get("cachedTokens", 0) or metadata.get("cachedInputTokens", 0) or 0
        )
        model_id = _strip_copilot_prefix(req.get("modelId") or "")

        if req.get("result") and not prompt_tokens and not output_tokens:
            resolved: str = metadata.get("resolvedModel") or model_id or "unknown"
            print(
                f"[WARNING] session {session_id} req[{req_idx}]: "
                f"promptTokens/outputTokens missing (model={resolved}), skipping request"
            )

        response_items: list[JsonObj] = req.get("response") or []

        thinking_chars = sum(
            len(item.get("value", ""))
            for item in response_items
            if item.get("kind") == "thinking" and isinstance(item.get("value"), str)
        )
        thinking_tokens = thinking_chars // CHARS_PER_THINKING_TOKEN

        tool_count = sum(
            1 for item in response_items if item.get("kind") in TOOL_RESPONSE_KINDS
        )

        if prompt_tokens or output_tokens:
            has_tokens = True
            cost = _calc_cost(prompt_tokens, output_tokens, cached_tokens, model_id)
            cost_parts.append(f"{cost:.4f}")
            total_input += prompt_tokens
            total_output += output_tokens
            total_thinking += thinking_tokens
            total_cached += cached_tokens

        total_tools += tool_count
        if model_id and model_id not in models:
            models.append(model_id)

    if not has_tokens:
        models_str = ", ".join(models) if models else "unknown"
        print(
            f"[WARNING] session {session_id}: no token data in any request "
            f"(models={models_str}), skipping session"
        )
        return None

    total_cost = sum(float(p) for p in cost_parts)

    file_uri = "file:///" + filepath.replace("\\", "/").lstrip("/")

    return SessionInfo(
        session_id=state.get("sessionId") or "",
        workspace_id=workspace_id,
        folder_path=unquote(folder_path),
        title=state.get("customTitle") or "",
        last_active=last_active,
        turns=len(requests),
        tools=total_tools,
        input=total_input,
        output=total_output,
        thinking=total_thinking,
        cached=total_cached,
        total=total_input + total_output,
        models=",".join(models) if models else "unknown",
        cost=total_cost,
        cost_detail="+".join(cost_parts),
        chat_session_path=file_uri,
    )


def scan_all_sessions() -> list[SessionInfo]:
    """Scan all VS Code workspaceStorage dirs and return parsed sessions."""
    sessions: list[SessionInfo] = []

    for ws_root in get_workspace_storage_dirs():
        try:
            workspace_ids = os.listdir(ws_root)
        except OSError:
            continue

        for workspace_id in workspace_ids:
            workspace_dir = os.path.join(ws_root, workspace_id)
            chat_dir = os.path.join(workspace_dir, "chatSessions")
            if not os.path.isdir(chat_dir):
                continue

            folder_path: FolderUri = ""
            wj_path = os.path.join(workspace_dir, "workspace.json")
            if os.path.isfile(wj_path):
                try:
                    with open(wj_path, encoding="utf-8") as f:
                        folder_path = json.load(f).get("folder", "")
                except Exception:
                    pass

            try:
                filenames = os.listdir(chat_dir)
            except OSError:
                continue

            for fname in filenames:
                if not fname.endswith(".jsonl"):
                    continue
                fpath = os.path.join(chat_dir, fname)
                try:
                    state = parse_jsonl(fpath)
                    session = extract_session(state, workspace_id, folder_path, fpath)
                    if session:
                        sessions.append(session)
                except Exception as e:
                    print(f"[WARNING] failed to parse {fpath}: {e}")

    return sessions


def _build_date_ranges(today: date) -> dict[str, DateRange]:
    month_start = today.replace(day=1)
    prev_month_end = month_start - timedelta(days=1)
    prev_month_start = prev_month_end.replace(day=1)
    return {
        "Today": DateRange(today, today),
        "Yesterday": DateRange(today - timedelta(days=1), today - timedelta(days=1)),
        "Last 30 Days": DateRange(today - timedelta(days=30), today),
        "Current Month": DateRange(month_start, today),
        "Previous Month": DateRange(prev_month_start, prev_month_end),
    }


def calculate_period_stats(sessions: list[SessionInfo]) -> dict[str, PeriodStats]:
    today = datetime.now().astimezone().date()
    ranges = _build_date_ranges(today)
    result: dict[str, PeriodStats] = {}
    for label, dr in ranges.items():
        subset = [s for s in sessions if dr.start <= s["last_active"].date() <= dr.end]
        result[label] = PeriodStats(
            cost=sum(s["cost"] for s in subset),
            sessions=len(subset),
            turns=sum(s["turns"] for s in subset),
        )
    return result


PERIODS = ["Today", "Yesterday", "Last 30 Days", "Current Month", "Previous Month"]


def generate_report(sessions: list[SessionInfo]) -> str:
    stats = calculate_period_stats(sessions)
    sorted_sessions = sorted(sessions, key=lambda s: s["last_active"], reverse=True)

    lines: list[str] = [
        "# AI USAGE REPORT",
        "",
        "## Usage Summary",
        "",
        "|" + "|".join(["Name"] + PERIODS) + "|",
        "|" + "|".join(["---"] * (len(PERIODS) + 1)) + "|",
    ]

    for metric in ("Cost", "Sessions", "Turns"):
        key = metric.lower()
        vals = [
            f"{stats[p].cost:.4f}" if key == "cost" else str(getattr(stats[p], key))
            for p in PERIODS
        ]
        lines.append("|" + metric + "|" + "|".join(vals) + "|")

    lines += [
        "",
        "## Usage Sessions",
        "",
        "|No|LastActive|Cost|Turns|Tools|Input|Output|Thinking|Cached|Total"
        "|Models|Title|FolderPath|WorkspaceId|ChatSession|ChatSessionPath|CostDetail|",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]

    for i, s in enumerate(sorted_sessions, 1):
        la = s["last_active"].strftime("%m/%d %H:%M")
        row = "|".join(
            [
                str(i),
                la,
                f"{s['cost']:.4f}",
                str(s["turns"]),
                str(s["tools"]),
                str(s["input"]),
                str(s["output"]),
                str(s["thinking"]),
                str(s["cached"]),
                str(s["total"]),
                s["models"],
                s["title"],
                s["folder_path"],
                s["workspace_id"],
                s["session_id"],
                s["chat_session_path"],
                s["cost_detail"],
            ]
        )
        lines.append("|" + row + "|")

    lines += [
        "",
        "---",
        "_All costs are in USD._",
    ]

    return "\n".join(lines) + "\n"


def main() -> None:
    sessions = scan_all_sessions()
    report = generate_report(sessions)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"Generated {OUTPUT_FILE} ({len(sessions)} sessions)")


if __name__ == "__main__":
    main()

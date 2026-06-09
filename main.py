"""
VSCode GitHub Copilot Chat の使用状況レポート生成 CLI
AI_USAGE_REPORT.md を生成する (外部ライブラリ不使用)
"""
import os
import json
from datetime import datetime, timezone, timedelta

OUTPUT_FILE = "AI_USAGE_REPORT.md"
CHARS_PER_THINKING_TOKEN = 5

# GitHub Copilot AI Credit pricing ($/million tokens)
# fallback: gpt-4o-mini
MODEL_PRICING = {
    "gpt-4o":            {"input": 5.0,   "output": 20.0,  "cached": 2.5},
    "gpt-4o-mini":       {"input": 0.15,  "output": 0.6,   "cached": 0.075},
    "gpt-4.1":           {"input": 2.0,   "output": 8.0,   "cached": 0.5},
    "gpt-4.1-mini":      {"input": 0.4,   "output": 1.6,   "cached": 0.1},
    "gpt-4.1-nano":      {"input": 0.1,   "output": 0.4,   "cached": 0.025},
    "gpt-5":             {"input": 1.25,  "output": 10.0},
    "gpt-5-mini":        {"input": 0.25,  "output": 2.0,   "cached": 0.025},
    "gpt-5.4":           {"input": 2.5,   "output": 15.0,  "cached": 0.25},
    "gpt-5.4-mini":      {"input": 0.75,  "output": 4.5,   "cached": 0.075},
    "o1":                {"input": 15.0,  "output": 60.0},
    "o1-mini":           {"input": 3.0,   "output": 12.0},
    "o3":                {"input": 2.0,   "output": 8.0},
    "o3-mini":           {"input": 1.1,   "output": 4.4},
    "o4-mini":           {"input": 1.1,   "output": 4.4},
    "claude-sonnet-4.5": {"input": 3.0,   "output": 15.0,  "cached": 0.3},
    "claude-sonnet-4.6": {"input": 3.0,   "output": 15.0,  "cached": 0.3},
    "claude-opus-4.5":   {"input": 5.0,   "output": 25.0,  "cached": 0.5},
    "claude-haiku-4.5":  {"input": 1.0,   "output": 5.0,   "cached": 0.1},
    "gemini-2.5-pro":    {"input": 1.25,  "output": 10.0,  "cached": 0.125},
    "gemini-2.5-flash":  {"input": 0.15,  "output": 0.6,   "cached": 0.0375},
    "gemini-2.0-flash":  {"input": 0.1,   "output": 0.4,   "cached": 0.025},
    "default":           {"input": 0.15,  "output": 0.6,   "cached": 0.075},
}


# ---------------------------------------------------------------------------
# VSCode workspaceStorage ディレクトリの取得
# ---------------------------------------------------------------------------

def get_vscode_workspace_dirs():
    appdata = os.environ.get("APPDATA", "")
    editions = ["Code", "Code - Insiders", "Code - Exploration"]
    dirs = []
    for edition in editions:
        ws = os.path.join(appdata, edition, "User", "workspaceStorage")
        if os.path.isdir(ws):
            dirs.append(ws)
    return dirs


# ---------------------------------------------------------------------------
# Delta JSONL パーサー
# ---------------------------------------------------------------------------

def _set_at_path(obj, keys, value):
    """kind=1: パス keys の末尾に value をセット"""
    for key in keys[:-1]:
        if isinstance(key, int):
            obj = obj[key]
        else:
            obj = obj.setdefault(key, {})
    last = keys[-1]
    if isinstance(obj, list):
        while len(obj) <= last:
            obj.append({})
        obj[last] = value
    else:
        obj[last] = value


def _append_at_path(obj, keys, value):
    """kind=2: パス keys の配列に value を追加"""
    for key in keys:
        if isinstance(key, int):
            obj = obj[key]
        else:
            obj = obj.setdefault(key, [])
    if isinstance(value, list):
        obj.extend(value)
    else:
        obj.append(value)


def _apply_delta(state, record):
    kind = record.get("kind")
    if kind == 0:
        state.update(record.get("v", {}))
    elif kind == 1:
        k = record.get("k")
        if k:
            try:
                _set_at_path(state, k, record.get("v"))
            except (IndexError, KeyError, TypeError):
                pass
    elif kind == 2:
        k = record.get("k")
        if k is not None:
            try:
                _append_at_path(state, k, record.get("v"))
            except (IndexError, KeyError, TypeError):
                pass


def parse_jsonl(filepath):
    """Delta JSONL ファイルを読み込んで最終 state を返す"""
    state = {}
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


# ---------------------------------------------------------------------------
# コスト計算
# ---------------------------------------------------------------------------

def _lookup_pricing(model_id):
    """モデル名でプライシングを取得。見つからなければ default を返す"""
    # 完全一致
    if model_id in MODEL_PRICING:
        return MODEL_PRICING[model_id]
    # 部分一致 (例: "claude-sonnet-4.5-20251022" → "claude-sonnet-4.5")
    for key in MODEL_PRICING:
        if key != "default" and model_id.startswith(key):
            return MODEL_PRICING[key]
    print(f"[WARNING] unknown model '{model_id}', falling back to default pricing")
    return MODEL_PRICING["default"]


def _calc_cost(prompt_tokens, output_tokens, cached_tokens, model_id):
    pricing = _lookup_pricing(model_id)
    uncached = max(0, prompt_tokens - cached_tokens)
    cost = (
        uncached * pricing["input"]
        + cached_tokens * pricing.get("cached", pricing["input"])
        + output_tokens * pricing["output"]
    ) / 1_000_000
    return cost


# ---------------------------------------------------------------------------
# セッションデータ抽出
# ---------------------------------------------------------------------------

def _model_display(model_id_raw):
    """modelId から表示用文字列を生成 (copilot/ prefix を除去)"""
    if not model_id_raw:
        return "unknown"
    return model_id_raw.replace("copilot/", "")


def extract_session(state, workspace_id, folder):
    """再構築済み state からセッション情報 dict を返す。不要なら None"""
    session_id = state.get("sessionId", workspace_id[:8])
    requests = state.get("requests") or []
    if not requests:
        return None

    # LastActive: 最後のリクエストの completedAt か timestamp (ms)
    last_active_ms = None
    for req in requests:
        ms = ((req.get("modelState") or {}).get("completedAt")
              or req.get("timestamp"))
        if ms and (last_active_ms is None or ms > last_active_ms):
            last_active_ms = ms
    if last_active_ms is None:
        print(f"[WARNING] session {session_id}: no timestamp found, skipping")
        return None

    last_active = datetime.fromtimestamp(
        last_active_ms / 1000, tz=timezone.utc
    ).astimezone()

    total_input = total_output = total_thinking = total_cached = total_tools = 0
    models = []
    cost_parts = []
    has_tokens = False

    for req_idx, req in enumerate(requests):
        metadata = ((req.get("result") or {}).get("metadata")) or {}
        prompt_tokens  = int(metadata.get("promptTokens",  0) or 0)
        output_tokens  = int(metadata.get("outputTokens",  0) or 0)
        cached_tokens  = int(
            metadata.get("cachedTokens", 0)
            or metadata.get("cachedInputTokens", 0)
            or 0
        )

        model_id = _model_display(req.get("modelId") or "")

        # result はあるが token フィールドが欠落しているケースを警告
        if req.get("result") and not prompt_tokens and not output_tokens:
            resolved = metadata.get("resolvedModel", model_id or "unknown")
            print(f"[WARNING] session {session_id} req[{req_idx}]: "
                  f"promptTokens/outputTokens missing (model={resolved}), skipping request")

        # Thinking トークン: response items の kind=thinking の value 文字数 ÷ 5
        thinking_chars = sum(
            len(item.get("value", ""))
            for item in (req.get("response") or [])
            if item.get("kind") == "thinking"
            and isinstance(item.get("value"), str)
        )
        thinking_tokens = thinking_chars // CHARS_PER_THINKING_TOKEN

        # Tools: toolInvocationSerialized + progressTaskSerialized
        tool_count = sum(
            1 for item in (req.get("response") or [])
            if item.get("kind") in ("toolInvocationSerialized", "progressTaskSerialized")
        )

        if prompt_tokens or output_tokens:
            has_tokens = True
            cost = _calc_cost(prompt_tokens, output_tokens, cached_tokens, model_id)
            cost_parts.append(f"{cost:.4f}")
            total_input    += prompt_tokens
            total_output   += output_tokens
            total_thinking += thinking_tokens
            total_cached   += cached_tokens

        total_tools += tool_count
        if model_id and model_id not in models:
            models.append(model_id)

    if not has_tokens:
        models_str = ", ".join(models) if models else "unknown"
        print(f"[WARNING] session {session_id}: no token data in any request "
              f"(models={models_str}), skipping session")
        return None

    total_cost = sum(float(x) for x in cost_parts)

    return {
        "session_id":   state.get("sessionId", ""),
        "workspace_id": workspace_id,
        "folder":       folder,
        "title":        state.get("customTitle", ""),
        "last_active":  last_active,
        "turns":        len(requests),
        "tools":        total_tools,
        "input":        total_input,
        "output":       total_output,
        "thinking":     total_thinking,
        "cached":       total_cached,
        "total":        total_input + total_output,
        "models":       ",".join(models) if models else "unknown",
        "cost":         total_cost,
        "cost_detail":  "+".join(cost_parts),
    }


# ---------------------------------------------------------------------------
# ワークスペーススキャン
# ---------------------------------------------------------------------------

def scan_all_sessions():
    sessions = []
    for ws_root in get_vscode_workspace_dirs():
        try:
            workspace_ids = os.listdir(ws_root)
        except OSError:
            continue
        for workspace_id in workspace_ids:
            workspace_dir = os.path.join(ws_root, workspace_id)
            chat_dir = os.path.join(workspace_dir, "chatSessions")
            if not os.path.isdir(chat_dir):
                continue

            # workspace.json からフォルダ URI を取得
            folder = ""
            wj_path = os.path.join(workspace_dir, "workspace.json")
            if os.path.isfile(wj_path):
                try:
                    with open(wj_path, encoding="utf-8") as f:
                        folder = json.load(f).get("folder", "")
                except Exception:
                    pass

            try:
                fnames = os.listdir(chat_dir)
            except OSError:
                continue
            for fname in fnames:
                if not fname.endswith(".jsonl"):
                    continue
                fpath = os.path.join(chat_dir, fname)
                try:
                    state = parse_jsonl(fpath)
                    session = extract_session(state, workspace_id, folder)
                    if session:
                        sessions.append(session)
                except Exception as e:
                    print(f"[WARNING] failed to parse {fpath}: {e}")
    return sessions


# ---------------------------------------------------------------------------
# 期間別集計
# ---------------------------------------------------------------------------

def calculate_period_stats(sessions):
    now = datetime.now().astimezone()
    today = now.date()
    yesterday = today - timedelta(days=1)
    month_start = today.replace(day=1)
    prev_month_end = month_start - timedelta(days=1)
    prev_month_start = prev_month_end.replace(day=1)
    last30_start = today - timedelta(days=30)

    def stats_for(sess_list):
        return {
            "cost":     sum(s["cost"] for s in sess_list),
            "sessions": len(sess_list),
            "turns":    sum(s["turns"] for s in sess_list),
        }

    def by_date(d):
        return [s for s in sessions if s["last_active"].date() == d]

    def by_range(start, end):
        return [s for s in sessions if start <= s["last_active"].date() <= end]

    return {
        "Today":          stats_for(by_date(today)),
        "Yesterday":      stats_for(by_date(yesterday)),
        "Last 30 Days":   stats_for(by_range(last30_start, today)),
        "Current Month":  stats_for(by_range(month_start, today)),
        "Previous Month": stats_for(by_range(prev_month_start, prev_month_end)),
    }


# ---------------------------------------------------------------------------
# レポート生成
# ---------------------------------------------------------------------------

def generate_report(sessions):
    stats = calculate_period_stats(sessions)
    sorted_sessions = sorted(sessions, key=lambda s: s["last_active"], reverse=True)

    periods = ["Today", "Yesterday", "Last 30 Days", "Current Month", "Previous Month"]
    lines = []

    lines.append("# AI USAGE REPORT")
    lines.append("")
    lines.append("## Usage Summary")
    lines.append("")

    # ヘッダー行: 空セル + 期間名
    lines.append("|" + "|".join([""] + periods) + "|")
    lines.append("|" + "|".join(["---"] * (len(periods) + 1)) + "|")
    for metric in ["Cost", "Sessions", "Turns"]:
        key = metric.lower()
        vals = []
        for p in periods:
            v = stats[p][key]
            vals.append(f"{v:.4f}" if key == "cost" else str(v))
        lines.append("|" + metric + "|" + "|".join(vals) + "|")

    lines.append("")
    lines.append("## Usage Sessions")
    lines.append("")
    lines.append(
        "|#|LastActive|Cost|Turns|Tools|Input|Output|Thinking|Cached|Total"
        "|Models|Title|Folder|WorkspaceId|ChatSession|CostDetail|"
    )
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")

    for i, s in enumerate(sorted_sessions, 1):
        la = s["last_active"].strftime("%m/%d %H:%M")
        row = "|".join([
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
            s["folder"],
            s["workspace_id"],
            s["session_id"],
            s["cost_detail"],
        ])
        lines.append("|" + row + "|")

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# エントリーポイント
# ---------------------------------------------------------------------------

def main():
    sessions = scan_all_sessions()
    report = generate_report(sessions)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"Generated {OUTPUT_FILE} ({len(sessions)} sessions)")


if __name__ == "__main__":
    main()

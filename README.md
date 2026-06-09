# VSCode AI Usage

A simple CLI tool that scans VS Code GitHub Copilot Chat session files and generates a Markdown usage report with token counts and costs per session.

## Requirements

- Python 3.11+
- No external dependencies (standard library only)

## Usage

```bash
python main.py
```

```bash
uv run https://raw.githubusercontent.com/chai3/vscode-ai-usage/main/main.py
```


Generates `AI_USAGE_REPORT.md` in the current directory. Overwrites any existing file.

## Output

### Usage Summary

Aggregated cost, session count, and turn count across five time windows.

| Period | Description |
|---|---|
| Today | Sessions last active on today's local date |
| Yesterday | Sessions last active on yesterday's local date |
| Last 30 Days | Rolling 30-day window ending today |
| Current Month | From the 1st of the current month to today |
| Previous Month | Full previous calendar month |

### Usage Sessions

All sessions sorted by **LastActive** descending.

| Column | Description |
|---|---|
| No |  Row number |
| LastActive |  Timestamp of the last completed request (`MM/DD HH:MM`) |
| Cost |  Estimated total cost in USD |
| Turns |  Number of requests in the session |
| Tools |  Number of tool invocations (`toolInvocationSerialized` + `progressTaskSerialized`) |
| Input |  Sum of `promptTokens` across all requests |
| Output |  Sum of `outputTokens` across all requests |
| Thinking |  Estimated thinking tokens (thinking response item text ÷ 5 chars/token) |
| Cached |  Sum of cached input tokens |
| Total |  Input + Output | 
| Models |  Comma-separated list of model IDs used (e.g. `auto`, `claude-sonnet-4.5`) |
| Title |  Session `customTitle | 
| FolderPath |  Workspace folder path decoded from the URI in `workspace.json | 
| WorkspaceId |  VS Code `workspaceStorage` directory hash |
| ChatSession |  Session UUID (matches the `.jsonl` filename) |
| ChatSessionPath |  Full path to the `.jsonl` file (e.g. `C:\Users\...\chatSessions\5783445c-....jsonl`) |
| CostDetail |  Per-request costs joined by `+`, requests with no token data omitted |

Example output:

```markdown
## Usage Summary

|Name|Today|Yesterday|Last 30 Days|Current Month|Previous Month|
|---|---|---|---|---|---|
|Cost|0.0068|0.0000|0.0068|0.0068|0.0000|
|Sessions|1|0|1|1|0|
|Turns|2|0|2|2|0|

## Usage Sessions

|No|LastActive|Cost|Turns|Tools|Input|Output|Thinking|Cached|Total|Models|Title|FolderPath|WorkspaceId|ChatSession|ChatSessionPath|CostDetail|
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
|1|06/09 20:24|0.0068|2|3|39660|1418|441|0|41078|auto|improve pyproject.toml|file:///c:/data/xxx|be1bb5473db454ff50f53eeeafaa2857|5783445c-a91c-4cbc-a2f8-c025fd336a61|file:///C:/Users/user/AppData/Roaming/Code/User/workspaceStorage/be1bb5473db454ff50f53eeeafaa2857/chatSessions/5783445c-a91c-4cbc-a2f8-c025fd336a61.jsonl|0.0036+0.0032|
|2|04/14 21:05|0.0123|3|8|75341|1678|569|0|77019|auto|Locating Large Files and Their Necessity for Windows|file:///c:/data/xxx|42d6fc9ca0cdfc1724d6249d442a68f6|797fc154-0bff-4bbc-bd0b-a829cd24ff41|file:///C:/Users/user/AppData/Roaming/Code/User/workspaceStorage/42d6fc9ca0cdfc1724d6249d442a68f6/chatSessions/797fc154-0bff-4bbc-bd0b-a829cd24ff41.jsonl|0.0038+0.0043+0.0042|
|3|04/14 20:30|0.0044|1|4|28517|223|295|0|28740|auto|Locating Large Files for Windows Operation|file:///c:/data/xxx|42d6fc9ca0cdfc1724d6249d442a68f6|5a37176a-e2c1-44c5-b7c3-773783cb1a15|file:///C:/Users/user/AppData/Roaming/Code/User/workspaceStorage/42d6fc9ca0cdfc1724d6249d442a68f6/chatSessions/5a37176a-e2c1-44c5-b7c3-773783cb1a15.jsonl|0.0044|

## Data Sources

Scans the following directories (if they exist):

```
%APPDATA%\Code\User\workspaceStorage\
%APPDATA%\Code - Insiders\User\workspaceStorage\
%APPDATA%\Code - Exploration\User\workspaceStorage\
~/Library/Application Support/Code/User/workspaceStorage/
~/Library/Application Support/Code - Insiders/User/workspaceStorage/
~/Library/Application Support/Code - Exploration/User/workspaceStorage/
~/.config/Code/User/workspaceStorage/
~/.config/Code - Insiders/User/workspaceStorage/
~/.config/Code - Exploration/User/workspaceStorage/
```

All paths are checked unconditionally; non-existent paths are silently skipped.

For each workspace, reads:

- `chatSessions/*.jsonl` — VS Code delta JSONL format (kind `0`/`1`/`2` patch records)
- `workspace.json` — provides the workspace folder URI

> **Note:** Only the delta JSONL format (`.jsonl`) introduced around late January 2026 is supported.
> Sessions stored in the legacy snapshot format (`.json`) used before that date are not parsed and will be silently ignored.

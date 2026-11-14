# koji-mcp

A read-only [MCP](https://modelcontextprotocol.io) server for the [Koji](https://forge.fedoraproject.org/koji/koji) build system. An AI assistant can use it to look up builds, tags, tasks and logs, and to explain why a build failed. Fedora's Koji is the default hub.

## Quick path

1. Install the dependencies (needs [uv](https://docs.astral.sh/uv/)):

   ```bash
   uv sync
   ```

2. Register the server with your MCP client. For Claude Code:

   ```bash
   claude mcp add koji -- uv --directory /path/to/koji-mcp run koji-mcp
   ```

3. Ask something like *"Why did bazelisk-1.30.0-4.fc44 fail?"* or *"Which bash build is in f41-updates?"*.

## Tools

Every tool is read-only and uses anonymous hub calls, so it needs no login.

| Tool | What it answers |
|------|-----------------|
| `koji_diagnose_failed_build` | Why a build or task failed: the failed subtask, Koji's error, the likely cause, key error lines and the `build.log` tail |
| `koji_get_build` | One build: state, owner, task, timestamps, web link |
| `koji_list_builds` | Builds filtered by package, owner, state, NVR prefix or dates |
| `koji_list_rpms` | The RPMs a build produced |
| `koji_latest_builds` | The latest build of each package in a tag |
| `koji_list_tagged` | Every build tagged into a tag |
| `koji_get_tag` / `koji_tag_inheritance` | A tag's configuration and its inheritance chain |
| `koji_get_target` | A build target's build tag and destination tag |
| `koji_get_task` | A task and its child tasks |
| `koji_task_logs` | A task's output files, or the tail of one log |
| `koji_search` | Exact names of packages, builds, tags, targets, users, hosts or RPMs |
| `koji_history` | Who tagged or untagged builds, or changed tags, and when |

Shared behavior:

- **Output**: each tool takes `response_format`: `markdown` (default) or `json` (the full record).
- **Paging**: list tools take `limit` and `offset`. Markdown output ends with the next offset, and JSON includes `has_more` and `next_offset`.
- **Dates**: all dates are ISO 8601 in UTC.
- **Logs**: logs are always truncated. The server reads at most the last 256 KiB and returns the last `tail_lines` lines, plus a link to the full log.

## Other Koji instances

The server reads standard koji client profiles from `/etc/koji.conf`, `/etc/koji.conf.d/` and `~/.koji/config.d/`. Choose a profile with `--profile` or with the `KOJI_MCP_PROFILE` environment variable:

```bash
claude mcp add koji-stream -- uv --directory /path/to/koji-mcp run koji-mcp --profile stream
```

If no configuration exists for the default `koji` profile (macOS, or a distro other than Fedora), the server uses Fedora's hub.

## Development

```bash
uv run pytest                               # unit and stdio protocol tests (offline)
KOJI_MCP_LIVE=1 uv run pytest tests/test_live_smoke.py   # read-only checks against Fedora Koji
npx @modelcontextprotocol/inspector uv run koji-mcp      # explore the tools interactively
```

Out of scope for now: write operations (`build`, `cancelTask`, `tagBuild`, ...), which need authentication.

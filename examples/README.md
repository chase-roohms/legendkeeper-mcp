# Example client configurations

| File | Client | How the server runs |
|---|---|---|
| [claude-desktop/uvx.json](claude-desktop/uvx.json) | Claude Desktop | `uvx legendkeeper-mcp` (PyPI) |
| [claude-desktop/docker.json](claude-desktop/docker.json) | Claude Desktop | Docker, stdio |
| [claude-desktop/multi-world.json](claude-desktop/multi-world.json) | Claude Desktop | Several worlds + a read-only instance |
| [vscode/mcp.json](vscode/mcp.json) | VS Code (`.vscode/mcp.json`) | Docker, key prompted securely |
| [vscode/mcp.uvx.json](vscode/mcp.uvx.json) | VS Code | `uvx`, key prompted securely |
| [cursor/mcp.json](cursor/mcp.json) | Cursor (`~/.cursor/mcp.json`) | `uvx` |
| [claude-code.sh](claude-code.sh) | Claude Code | `claude mcp add …` commands |
| [http/client-config.json](http/client-config.json) | Any client with remote/HTTP support | `docker compose up -d` |
| [http/vscode-mcp.json](http/vscode-mcp.json) | VS Code | `docker compose up -d` |

Claude Desktop's config lives at `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) or
`%APPDATA%\Claude\claude_desktop_config.json` (Windows). Merge the `mcpServers` entry into it and restart Claude.

Replace `lk_your_key_here` with your key, or better, use a client that prompts for secrets (VS Code `inputs`).
Never commit real keys.

Until the package is on PyPI you can run straight from GitHub:
`uvx --from git+https://github.com/chase-roohms/legendkeeper-mcp legendkeeper-mcp`.

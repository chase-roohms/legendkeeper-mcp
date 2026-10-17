# Security policy

## Reporting a vulnerability

Please report security problems privately via
[GitHub security advisories](https://github.com/chase-roohms/legendkeeper-mcp/security/advisories/new) rather than
public issues. You should get a response within a week.

## Scope and design notes

- The server holds LegendKeeper API keys, which grant read/write access to your worlds. Treat the machine and the
  MCP client configuration that contain them accordingly. Prefer clients that store secrets securely (for
  example, VS Code `inputs`) and `*_FILE` variables / Docker secrets over plain-text config.
- In HTTP mode, always set `MCP_AUTH_TOKEN`. The server refuses to listen on a non-loopback address without one
  unless you explicitly set `MCP_ALLOW_UNAUTHENTICATED=true`.
- `upload_image` can read local files that the server process can read. Set `LEGENDKEEPER_UPLOAD_ROOT` to limit it
  to one directory (the Docker image limits it to `/uploads`). Only real image files are uploaded.
- An assistant connected to this server acts with the key's permissions. Use read-only mode or a Member-role key
  when you don't need write access, and review destructive actions (they require `confirm: true`).
- Be careful when connecting other MCP servers that fetch untrusted content (web pages, email) in the same
  session. Prompt-injected instructions could ask the assistant to modify your world.

#!/usr/bin/env sh
# Register the server with Claude Code (https://docs.claude.com/en/docs/claude-code/mcp).
# Pick ONE of the following.

# 1. Published package via uvx:
claude mcp add legendkeeper --env LEGENDKEEPER_API_KEY="$LEGENDKEEPER_API_KEY" -- uvx legendkeeper-mcp

# 2. Docker (stdio):
# claude mcp add legendkeeper --env LEGENDKEEPER_API_KEY="$LEGENDKEEPER_API_KEY" -- \
#   docker run -i --rm -e LEGENDKEEPER_API_KEY ghcr.io/chase-roohms/legendkeeper-mcp:latest

# 3. A running HTTP container (docker compose up -d):
# claude mcp add --transport http legendkeeper http://localhost:8000/mcp \
#   --header "Authorization: Bearer $MCP_AUTH_TOKEN"

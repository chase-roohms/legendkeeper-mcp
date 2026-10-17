# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- CI runs the live API test suite against a real project when the `LEGENDKEEPER_API_KEY` repository secret is
  set (also weekly and before every release).

### Fixed

- `[[links]]` between items of one `create_resources` batch now resolve.
- `read_document` prefers maps, timelines and boards over empty documents; `browse_tree` no longer shows `{blank}`.
- `move_resource` and `create_resource` accept sibling names for `before`/`after`.
- `upload_image` accepts Windows paths.

## [0.1.0] - 2026-10-16

### Added

- MCP server for the LegendKeeper v3 API with 31 tools: discovery (`browse_tree`, `search`, `get_resource`, tags,
  templates, calendars, members, assets), `read_document` for pages/maps/timelines/boards, `write_page` with
  append/prepend/replace/section modes, resource CRUD with batch creation and templates, `set_facts` for infobox
  and sidebar facts, map features, timeline events and lanes, `upload_image`, and the `api_request` escape hatch.
- Two-way Markdown conversion with mentions (`[[Name]]`), callouts, secrets, collapsible sections, task lists,
  tables, images, raw `lk-block` fences and lossless `lk:keep` preservation of blocks Markdown cannot express.
- Multiple projects via `LEGENDKEEPER_API_KEY_<NAME>`, read-only mode, `_FILE` secrets.
- stdio and bearer-token protected streamable HTTP transports; `legendkeeper-mcp check` command.
- Docker image and compose file; GitHub Actions for CI, releases (PyPI + GHCR) and weekly API spec drift checks.

[Unreleased]: https://github.com/chase-roohms/legendkeeper-mcp/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/chase-roohms/legendkeeper-mcp/releases/tag/v0.1.0

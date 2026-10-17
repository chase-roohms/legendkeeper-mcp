# Contributing

Thanks for helping! This is a small, unofficial project; issues and pull requests are welcome.

## Setup

```bash
uv sync
uv run pytest
```

## Before opening a pull request

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest
```

- Add or update tests with every change. Tool changes belong in `tests/test_tools.py`, which runs against
  `tests/fake_lk.py`. When you call a new endpoint, add a handler to the fake. The fake rejects any request that
  doesn't match LegendKeeper's OpenAPI spec, so mistakes in paths and bodies surface as test failures.
- Markdown conversion changes need round-trip tests in `tests/test_markdown.py`. The Hypothesis tests in
  `tests/test_markdown_fuzz.py` must keep passing.
- Keep tool descriptions short, concrete and written for an LLM: what the tool does, when to use it, and what
  the arguments mean.
- Update `README.md` and `CHANGELOG.md` (under *Unreleased*) for user-visible changes.

## Live testing

If you have a LegendKeeper project on a paid plan, you can run the opt-in live suite. It creates one
`[mcp-test] …` sandbox resource and deletes it afterwards:

```bash
LEGENDKEEPER_LIVE_TESTS=1 LEGENDKEEPER_LIVE_WRITE=1 LEGENDKEEPER_API_KEY=lk_... uv run pytest tests/test_live.py -v
```

Never commit API keys. `.env` is git-ignored.

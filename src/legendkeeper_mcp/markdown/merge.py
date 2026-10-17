"""Combine parsed Markdown with an existing document so that writes are as lossless as possible.

* ``lk:keep`` sentinels are replaced by the original block from the current document.
* A parsed top-level block whose Markdown rendering equals that of an existing block is replaced by
  the existing block, so ids and styling Markdown cannot express (colors, alignment, callout icons…)
  survive a full-page rewrite of unchanged content.
* Section helpers locate a heading and the blocks under it.
"""

from __future__ import annotations

import copy
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from .inline import plain_text
from .parse import KEEP
from .render import RenderContext, is_expressible, keep_summary, render_top_block

Block = dict[str, Any]


@dataclass
class MaterializeResult:
    blocks: list[Block]
    warnings: list[str] = field(default_factory=list)
    reused: int = 0
    kept: int = 0
    created: int = 0


def iter_blocks(blocks: list[Block]) -> Iterator[Block]:
    for block in blocks or []:
        yield block
        yield from iter_blocks(block.get("children") or [])


def materialize(
    parsed: list[Block],
    existing: list[Block] | None,
    ctx: RenderContext | None = None,
    *,
    reuse_unchanged: bool = True,
) -> MaterializeResult:
    ctx = ctx or RenderContext()
    existing = existing or []
    by_id = {b["id"]: b for b in iter_blocks(existing) if b.get("id")}
    canonical: dict[str, list[Block]] = defaultdict(list)
    if reuse_unchanged:
        for block in existing:
            if is_expressible(block):
                canonical[render_top_block(block, ctx)].append(block)

    result = MaterializeResult(blocks=[])
    used: set[str] = set()

    for block in parsed:
        if block.get("kind") == KEEP:
            block_id = str(block.get("id"))
            original = by_id.get(block_id)
            if original is None:
                result.warnings.append(
                    f"lk:keep marker for block '{block_id}' does not match a block in this document; it was dropped."
                )
                continue
            if block_id in used:
                result.warnings.append(f"Block '{block_id}' was referenced twice; the duplicate was dropped.")
                continue
            region = block.get("region")
            if region is not None and region.strip() != keep_summary(original, ctx).strip():
                result.warnings.append(
                    f"Text inside the lk:keep region for {original.get('kind')} block '{block_id}' was edited, "
                    "but kept blocks are written back unchanged. Use set_facts for infobox facts, or read the "
                    "page with opaque_blocks='json' and edit the lk-block JSON instead."
                )
            _claim_ids(original, used)
            result.blocks.append(original)
            result.kept += 1
            continue

        if block.get("id"):
            ids = {b.get("id") for b in iter_blocks([block]) if b.get("id")}
            if ids & used:
                block = _strip_ids(block)
                result.warnings.append("A block id appeared twice; the copy was given a new id.")
            _claim_ids(block, used)
            result.blocks.append(block)
            result.created += 1
            continue

        if reuse_unchanged and is_expressible(block):
            key = render_top_block(block, ctx)
            match = next((b for b in canonical.get(key, []) if b.get("id") not in used), None)
            if match is not None:
                _claim_ids(match, used)
                result.blocks.append(match)
                result.reused += 1
                continue
        result.blocks.append(block)
        result.created += 1

    return result


def _claim_ids(block: Block, used: set[str]) -> None:
    for b in iter_blocks([block]):
        if b.get("id"):
            used.add(str(b["id"]))


def _strip_ids(block: Block) -> Block:
    clone = copy.deepcopy(block)
    for b in iter_blocks([clone]):
        b.pop("id", None)
    return clone


def drop_keep_sentinels(blocks: list[Block]) -> tuple[list[Block], list[str]]:
    """For brand-new documents: there is nothing to keep."""
    out = [b for b in blocks if b.get("kind") != KEEP]
    warnings = []
    if len(out) != len(blocks):
        warnings.append("lk:keep markers only work when editing an existing page; they were ignored.")
    return out, warnings


@dataclass(frozen=True)
class Section:
    start: int  # index of the heading block
    end: int  # exclusive end of the section body
    level: int
    title: str


def heading_title(block: Block) -> str:
    return " ".join(plain_text(block.get("content") or []).split())


def list_headings(blocks: list[Block]) -> list[tuple[int, int, str]]:
    return [
        (i, int(b.get("level") or 1), heading_title(b))
        for i, b in enumerate(blocks)
        if b.get("kind") == "heading"
    ]


def find_section(blocks: list[Block], title: str) -> Section | None:
    wanted = " ".join(title.strip().lstrip("#").split()).casefold()
    headings = list_headings(blocks)
    for pos, (index, level, text) in enumerate(headings):
        if text.casefold() != wanted:
            continue
        end = len(blocks)
        for later_index, later_level, _ in headings[pos + 1 :]:
            if later_level <= level:
                end = later_index
                break
        return Section(start=index, end=end, level=level, title=text)
    return None

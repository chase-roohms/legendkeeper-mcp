"""Guidance text exposed to the model (server instructions and MCP resources)."""

SERVER_INSTRUCTIONS = """\
Tools for a LegendKeeper world (wiki pages, maps, timelines, boards). Unofficial integration.

Model: a project (world) holds a tree of *resources*. Each resource has 1-5 *documents*: `page` (rich text),
`map` (image with pins/regions/paths/labels), `time` (timeline lanes and events), `board` (whiteboard), or
`blank`. Tools accept a resource id or its exact name. Use search or browse_tree to find things first.

Pages are read and written as Markdown (see the legendkeeper://guides/markdown resource):
- Link to other resources with [[Exact Name]] or [label](lk://resources/<id>). This makes real LegendKeeper
  mentions.
- Callouts: `> [!info|note|success|warning|error]`. GM-only text: `> [!secret]`. Collapsible: `> [!expand] Title`.
- read_document shows blocks Markdown cannot express (infoboxes, embeds, collections...) as
  `<!-- lk:keep id=... -->` regions. Leave those markers in place when rewriting a page and the original block
  is preserved. Edit infobox/sidebar facts with set_facts.
- Prefer write_page with mode=append/replace_section/append_to_section over rewriting whole pages.

Style: reuse existing tags (list_tags) and their exact spelling. Give resources an icon. Put at-a-glance facts in
an infobox (set_facts target=infobox). Link timeline events and map pins to their pages. Keep event names short.
Destructive tools need confirm=true; ask the user first. Deleted resources go to the trash and can be restored.
"""

MARKDOWN_GUIDE = """\
# LegendKeeper Markdown dialect

LegendKeeper stores pages as typed blocks. This server converts between Markdown and those blocks.

## Blocks
| Markdown | LegendKeeper block |
|---|---|
| `# Heading` … `###### Heading` | heading (levels 1-6) |
| plain paragraphs | paragraph |
| `- item`, `1. item` (nested lists allowed) | bullet / ordered list |
| `- [ ] task`, `- [x] done` | task list |
| `> quote` | quote |
| `> [!info]`, `[!note]`, `[!success]`/`[!tip]`, `[!warning]`, `[!error]`/`[!caution]`, `[!custom]` | callout |
| `> [!secret]` | secret block (GM-only, hidden from players and public pages) |
| `> [!expand] Title` | collapsible section |
| ```` ```lang ```` fenced code | code block |
| `---` | horizontal rule |
| GFM tables (`<br>` for line breaks in cells) | table |
| `![alt](url)` on its own line | image (URL must be a LegendKeeper asset URL, see upload_image) |
| ```` ```lk-block ```` fence with JSON | any raw LegendKeeper block (object or array), e.g. infobox, embed, meter |

## Inline
`**bold**`, `*italic*`, `~~strike~~`, `` `code` ``, `<u>underline</u>`, `<sub>`, `<sup>`,
`<span style="color: #RRGGBB; background-color: #RRGGBB">colored</span>`, `[text](https://url)`.
A line ending in `\\` (or two spaces) is a hard line break.

## Links between pages (mentions)
- `[[Exact Resource Name]]` or `[[Exact Resource Name|shown text]]` resolves by name or alias. If nothing matches,
  the text is left as plain text and you get a warning.
- `[shown text](lk://resources/<resourceId>)`, optionally `/docs/<documentId>`, links to an exact resource.

## Preserved blocks
When reading, blocks that Markdown cannot express appear as:

    <!-- lk:keep id="abc123" kind="infobox" -->
    …readable summary…
    <!-- /lk:keep -->

Keep the two marker lines (the summary can stay or go) to preserve the block exactly when writing. Edits inside
the region are ignored. To change these blocks, use set_facts (infobox / key-value facts), or read with
`opaque_blocks="json"` and edit the ```` ```lk-block ```` JSON.

Unchanged paragraphs, lists and so on are matched to the existing blocks, so their styling (colors, alignment,
callout icons) survives a full rewrite.
"""

STYLE_GUIDE = """\
# Conventions for content that looks native in LegendKeeper

- **Tags**: read list_tags first and reuse existing tags with the exact same spelling and case. New tags are
  short, Title Case, singular nouns, one per facet (Type: NPC/Location/Item/Faction/Event; Place; Status/Role;
  Arc/Session). Don't add a tag that repeats the parent's or resource's name.
- **Icons**: give every resource an icon_glyph (a Font Awesome name such as "crown", "dragon", "dungeon")
  and an icon_color (#RRGGBB). Use the same glyph and color for all resources of one kind.
- **Infobox**: at-a-glance facts (title, age, ruler, population…) go into an infobox at the top of the page
  (set_facts target="infobox"). Long prose stays in the page body. For a resource that is a single wiki page,
  prefer an infobox over sidebar properties.
- **Links**: link the pages you mention in text ([[Name]]). Link map pins and timeline events to their pages.
- **Parent pages**: a section page whose children hold the content should get a short introduction.
- **Map pins**: by default a pin is a bare glyph (icon_shape "none") with a Font Awesome icon_glyph. Use shapes
  and colors only when asked.
- **Timeline events**: give events short names only (events have no description). Put the longer account on a
  page and link the event to it.
- **Secrets**: GM-only information goes in `> [!secret]` blocks.
"""

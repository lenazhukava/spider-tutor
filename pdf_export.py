"""
Renders the markdown notes produced by create_draft_notes() (ChapterNotes.notes)
into a formatted PDF using reportlab — a pure-Python PDF library with no system
binary dependencies (no Cairo/Pango/wkhtmltopdf required).

The markdown string is parsed into structural blocks (headings, bullet/numbered
list items, paragraphs) rather than dumped as raw text, so headings, nested
lists, bold terms, and italic emphasis all get their own reportlab styling
instead of a flat wall of monospace-ish text.
"""

import itertools
import os
import re
import tempfile
from io import BytesIO

import matplotlib
matplotlib.use("Agg")  # headless — no display/GUI backend available on a server
from matplotlib import mathtext
from matplotlib.font_manager import FontProperties
from matplotlib.mathtext import MathTextParser

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    ListFlowable,
    ListItem,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
)

##################################################
#
# Unicode fallback
#
##################################################

# reportlab's built-in (Base14) fonts only cover WinAnsi/cp1252 — plain Latin
# text, accented letters, em/en dashes, curly quotes, and a handful of math
# symbols (± ° ² ³ × ÷) all render fine natively. Arrows, Greek letters, and
# less common math operators do not, and reportlab raises rather than
# silently dropping them — so those get transliterated to ASCII equivalents
# first. This keeps the dependency footprint to just reportlab (no bundled
# Unicode TTF font); see the caller-facing notes for the tradeoff.
_UNICODE_FALLBACKS = {
    "→": "->", "←": "<-", "↔": "<->", "⇒": "=>", "⇐": "<=", "↑": "^", "↓": "v",
    "Δ": "Delta ", "δ": "delta ", "Σ": "Sum ", "π": "pi", "θ": "theta",
    "α": "alpha", "β": "beta", "γ": "gamma", "λ": "lambda", "μ": "mu",
    "φ": "phi", "ω": "omega", "Ω": "Omega", "σ": "sigma",
    "≈": "~=", "≠": "!=", "≥": ">=", "≤": "<=", "∞": "infinity",
    "√": "sqrt ", "∂": "d", "∑": "Sum", "∏": "Product", "∫": "integral of ",
    "•": "-", "★": "*", "☆": "*",
}


def _sanitize_unicode(text: str) -> str:
    for src, repl in _UNICODE_FALLBACKS.items():
        if src in text:
            text = text.replace(src, repl)
    # Safety net for anything still outside cp1252 — replaced rather than
    # left to crash reportlab's Base14 font encoder.
    return text.encode("cp1252", errors="replace").decode("cp1252")


##################################################
#
# Math rendering (matplotlib mathtext -> PNG -> inline <img>)
#
##################################################

# The notes are rendered client-side with MathJax using the same $...$ / $$...$$
# delimiters (see templates/index.html's MathJax config) — mirroring that
# convention here is what makes the PDF match what the student already sees
# on the page, instead of leaving LaTeX source as literal text.
_DISPLAY_MATH_RE = re.compile(r"\$\$(.+?)\$\$", re.DOTALL)
_INLINE_MATH_RE = re.compile(r"\$([^$\n]+?)\$")
_BARE_NUMBER_RE = re.compile(r"[\d,]+(?:\.\d+)?")
# Signals that the span crosses two unrelated $-uses (e.g. currency) rather
# than being one real math expression: an arrow (real LaTeX spells that
# \to/\rightarrow, never a literal arrow glyph or "->"), or a natural-language
# connector word that essentially never appears inside actual math source.
_PROSE_SIGNAL_RE = re.compile(
    r"->|-->|→|↔|⇒|\s(?:and|or|to|the|a|an|is|was|with|of|in|for)\s", re.IGNORECASE
)

# Placeholder is pure ASCII letters/digits so it survives cp1252 sanitizing,
# XML escaping, and the bold/italic/code regexes untouched — it's only ever
# resolved to a real <img> tag in the final pass, after everything else that
# could otherwise corrupt an embedded "<...>" tag has already run.
_MATH_PLACEHOLDER = "MATHPLACEHOLDERZZ{}ZZEND"
_MATH_PLACEHOLDER_RE = re.compile(r"MATHPLACEHOLDERZZ(\d+)ZZEND")

_MATH_PARSER = MathTextParser("path")
DISPLAY_MATH_SCALE = 1.25


def _looks_like_math(expr: str) -> bool:
    """
    Guards against the main false-positive: two unrelated $-uses (usually
    currency, e.g. "Price $10 -> $12") getting paired up by a naive $...$
    scan into one bogus "expression" spanning from the first $ to the second.
    Rejects a bare number (e.g. the lone "10" in "$10$") and anything that
    reads like prose crossed the pairing (an arrow, or a connector word that
    would never appear inside real LaTeX source). Anything else is treated
    as math, same as MathJax's own inlineMath convention — which has this
    exact ambiguity too; no purely textual heuristic resolves it perfectly.
    """
    stripped = expr.strip()
    if not stripped:
        return False
    if _BARE_NUMBER_RE.fullmatch(stripped):
        return False
    if _PROSE_SIGNAL_RE.search(stripped):
        return False
    return True


def _measure_math(tex: str, fontsize: float):
    """Returns (width, height, depth) in points for a $-wrapped math string."""
    prop = FontProperties(size=fontsize)
    width, height, depth, _glyphs, _rects = _MATH_PARSER.parse(tex, dpi=72, prop=prop)
    return width, height, depth


def _render_math_png(tex: str, fontsize: float, out_path: str, dpi: int = 200) -> None:
    prop = FontProperties(size=fontsize)
    mathtext.math_to_image(tex, out_path, dpi=dpi, prop=prop)


def _extract_math(text: str, fontsize: float, jobs: list) -> str:
    """
    Replaces $$...$$ and $...$ math spans with plain-ASCII placeholder
    tokens, skipping bare-number spans that are almost certainly currency.
    Appends a (tex, fontsize) render job per placeholder to `jobs` (shared
    across one Paragraph's worth of text) and returns the token to embed.
    Runs before sanitize/escape/bold/italic so LaTeX source (backslashes,
    braces, ^, _) never gets mangled by passes meant for prose.
    """
    def repl_display(m):
        token = _MATH_PLACEHOLDER.format(len(jobs))
        jobs.append((f"${m.group(1).strip()}$", fontsize * DISPLAY_MATH_SCALE))
        return token

    def repl_inline(m):
        expr = m.group(1)
        if not _looks_like_math(expr):
            return m.group(0)
        token = _MATH_PLACEHOLDER.format(len(jobs))
        jobs.append((f"${expr}$", fontsize))
        return token

    text = _DISPLAY_MATH_RE.sub(repl_display, text)
    text = _INLINE_MATH_RE.sub(repl_inline, text)
    return text


def _resolve_math_placeholders(text: str, jobs: list, media_dir: str, counter: "itertools.count") -> str:
    """Renders each queued math job to a PNG and swaps its placeholder for
    the real reportlab <img> tag — done last, after escaping/bold/italic."""
    def repl(m):
        tex, fontsize = jobs[int(m.group(1))]
        width_pt, height_pt, depth_pt = _measure_math(tex, fontsize)
        path = os.path.join(media_dir, f"math_{next(counter)}.png")
        _render_math_png(tex, fontsize, path)
        # valign is a raw point offset of the image's bottom edge relative to
        # the baseline — negative sinks it by the glyph's descent (e.g. a
        # subscript's tail or a fraction's bottom) so it lines up with the text.
        return (
            f'<img src="{path}" width="{width_pt:.2f}" height="{height_pt:.2f}" '
            f'valign="{-depth_pt:.2f}"/>'
        )

    return _MATH_PLACEHOLDER_RE.sub(repl, text)


##################################################
#
# Markdown inline formatting -> reportlab mini-markup
#
##################################################

_INLINE_CODE_RE = re.compile(r"`([^`]+)`")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_ITALIC_RE = re.compile(r"\*(.+?)\*")


def _escape_xml(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _inline_to_markup(text: str, fontsize: float, media_dir: str, counter: "itertools.count") -> str:
    """
    Converts $math$/$$math$$, **bold**, *italic*, and `code` spans into
    reportlab's Paragraph mini-markup (<img>, <b>, <i>, <font face="Courier">).

    Order matters: math is pulled out first — as literal LaTeX source, before
    any escaping or markdown transforms touch it — and swapped for a plain
    placeholder token. Escaping/code/bold/italic then run on the surrounding
    prose only. The placeholder is resolved to a real <img> tag (rendered via
    matplotlib's mathtext) last, so its "<...>" markup can't be escaped away.
    """
    math_jobs = []
    text = _extract_math(text, fontsize, math_jobs)
    text = _sanitize_unicode(text)
    text = _escape_xml(text)
    text = _INLINE_CODE_RE.sub(lambda m: f'<font face="Courier">{m.group(1)}</font>', text)
    text = _BOLD_RE.sub(lambda m: f"<b>{m.group(1)}</b>", text)
    text = _ITALIC_RE.sub(lambda m: f"<i>{m.group(1)}</i>", text)
    if math_jobs:
        text = _resolve_math_placeholders(text, math_jobs, media_dir, counter)
    return text


##################################################
#
# Markdown block parsing
#
##################################################

_HEADING_RE = re.compile(r"^(#{1,4})\s+(.*)$")
_NUMBERED_RE = re.compile(r"^(\s*)\d+\.\s+(.*)$")
_BULLET_RE = re.compile(r"^(\s*)[-*]\s+(.*)$")


def _is_structural(line: str) -> bool:
    return bool(_HEADING_RE.match(line) or _NUMBERED_RE.match(line) or _BULLET_RE.match(line))


def _parse_blocks(markdown_text: str) -> list[dict]:
    """
    Parses markdown into a flat list of blocks:
      {"type": "heading", "level": 1-4, "text": ...}
      {"type": "list_item", "indent": int, "ordered": bool, "text": ...}
      {"type": "paragraph", "text": ...}
    Wrapped continuation lines (no blank line, not a new structural line) are
    folded into the preceding block's text.
    """
    lines = markdown_text.replace("\r\n", "\n").split("\n")
    blocks: list[dict] = []
    i = 0
    n = len(lines)

    while i < n:
        line = lines[i]
        if not line.strip():
            i += 1
            continue

        heading_m = _HEADING_RE.match(line)
        if heading_m:
            blocks.append({
                "type": "heading",
                "level": len(heading_m.group(1)),
                "text": heading_m.group(2).strip(),
            })
            i += 1
            continue

        numbered_m = _NUMBERED_RE.match(line)
        bullet_m = None if numbered_m else _BULLET_RE.match(line)
        if numbered_m or bullet_m:
            m = numbered_m or bullet_m
            indent = len(m.group(1))
            text = m.group(2).strip()
            j = i + 1
            while j < n and lines[j].strip() and not _is_structural(lines[j]):
                text += " " + lines[j].strip()
                j += 1
            blocks.append({
                "type": "list_item",
                "indent": indent,
                "ordered": bool(numbered_m),
                "text": text,
            })
            i = j
            continue

        # Plain paragraph line — fold in any wrapped continuation lines.
        text = line.strip()
        j = i + 1
        while j < n and lines[j].strip() and not _is_structural(lines[j]):
            text += " " + lines[j].strip()
            j += 1
        blocks.append({"type": "paragraph", "text": text})
        i = j

    return blocks


##################################################
#
# Blocks -> reportlab flowables
#
##################################################

_HEADING_STYLE_KEYS = {1: "H1", 2: "H2", 3: "H3", 4: "H4"}


def _build_styles() -> dict:
    base = getSampleStyleSheet()
    navy = colors.HexColor("#122851")
    red = colors.HexColor("#990000")

    styles = {
        "DocTitle": ParagraphStyle(
            "DocTitle", parent=base["Title"], fontSize=20, leading=24,
            spaceAfter=14, textColor=navy, alignment=TA_LEFT,
        ),
        "H1": ParagraphStyle(
            "H1", parent=base["Heading1"], fontSize=16, leading=20,
            spaceBefore=16, spaceAfter=8, textColor=navy,
        ),
        "H2": ParagraphStyle(
            "H2", parent=base["Heading2"], fontSize=13.5, leading=17,
            spaceBefore=14, spaceAfter=7, textColor=red,
        ),
        "H3": ParagraphStyle(
            "H3", parent=base["Heading3"], fontSize=12, leading=15,
            spaceBefore=10, spaceAfter=5, textColor=colors.HexColor("#333333"),
        ),
        "H4": ParagraphStyle(
            "H4", parent=base["Heading4"], fontSize=11, leading=14,
            spaceBefore=8, spaceAfter=4, textColor=colors.HexColor("#333333"),
        ),
        "Body": ParagraphStyle(
            "Body", parent=base["BodyText"], fontSize=10.5, leading=15.5,
            spaceAfter=7,
        ),
    }
    styles["ListItem"] = ParagraphStyle(
        "ListItem", parent=styles["Body"], spaceAfter=4, leading=14.5,
    )
    return styles


def _build_lists_at_indent(items: list[dict], idx: int, indent: int, styles: dict, media_dir: str, counter: "itertools.count"):
    """
    Recursively consumes consecutive list_item blocks at exactly `indent`,
    folding any more-deeply-indented run immediately following an item into
    nested ListFlowable(s) under that item. Returns (list_of_flowables, next_idx).

    Splits into a new ListFlowable whenever ordered/unordered changes within
    the same indent level (e.g. a "-" run immediately followed by a "1." run
    with no blank line between) — otherwise the whole run would render with
    whichever type its first item happened to be.

    Deliberately leaves leftIndent at reportlab's default: a nested
    ListFlowable placed inside a ListItem's content already renders indented
    relative to that item's text, and an explicit per-depth leftIndent here
    fights that (verified empirically — it shifts nested levels *left*
    instead of right).
    """
    flowables = []
    entries = []
    current_ordered = None

    def flush():
        if entries:
            bullet_type = "1" if current_ordered else "bullet"
            flowables.append(ListFlowable(
                entries[:],
                bulletType=bullet_type,
                start=1 if bullet_type == "1" else None,
                bulletFontSize=9,
                spaceBefore=2,
                spaceAfter=2,
            ))
            entries.clear()

    while idx < len(items) and items[idx]["indent"] == indent:
        item = items[idx]
        if current_ordered is None:
            current_ordered = item["ordered"]
        elif item["ordered"] != current_ordered:
            flush()
            current_ordered = item["ordered"]
        idx += 1

        markup = _inline_to_markup(item["text"], styles["ListItem"].fontSize, media_dir, counter)
        content = [Paragraph(markup, styles["ListItem"])]
        if idx < len(items) and items[idx]["indent"] > indent:
            sub_flowables, idx = _build_lists_at_indent(items, idx, items[idx]["indent"], styles, media_dir, counter)
            content.extend(sub_flowables)

        entries.append(ListItem(content))

    flush()
    return flowables, idx


def _blocks_to_story(blocks: list[dict], styles: dict, media_dir: str, counter: "itertools.count") -> list:
    story = []
    i = 0
    n = len(blocks)

    while i < n:
        block = blocks[i]

        if block["type"] == "heading":
            style = styles[_HEADING_STYLE_KEYS.get(block["level"], "H4")]
            markup = _inline_to_markup(block["text"], style.fontSize, media_dir, counter)
            story.append(Paragraph(markup, style))
            i += 1

        elif block["type"] == "list_item":
            run = []
            j = i
            while j < n and blocks[j]["type"] == "list_item":
                run.append(blocks[j])
                j += 1
            flowables, _ = _build_lists_at_indent(run, 0, run[0]["indent"], styles, media_dir, counter)
            story.extend(flowables)
            story.append(Spacer(1, 6))
            i = j

        else:  # paragraph
            markup = _inline_to_markup(block["text"], styles["Body"].fontSize, media_dir, counter)
            story.append(Paragraph(markup, styles["Body"]))
            i += 1

    return story


##################################################
#
# Public entry point
#
##################################################

def build_notes_pdf(notes_markdown: str, title: str = "Study Notes") -> bytes:
    """
    Renders a ChapterNotes.notes markdown string into a formatted PDF.

    Args:
        notes_markdown (str): The notes body (markdown) — headings, bullet/
            numbered lists, **bold**, *italic*, `inline code`, and $math$ /
            $$math$$ are parsed into their own reportlab elements (math is
            rendered to inline PNGs via matplotlib's mathtext) rather than
            dumped as flat text.
        title (str): Document title, printed as a heading at the top of the PDF.

    Returns:
        bytes: The rendered PDF file content.
    """
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=LETTER,
        leftMargin=0.9 * inch,
        rightMargin=0.9 * inch,
        topMargin=0.85 * inch,
        bottomMargin=0.85 * inch,
        title=title,
    )

    styles = _build_styles()

    # Math is rendered to standalone PNG files that the <img> tags reference
    # by path — they only need to exist while doc.build() is drawing, so the
    # temp dir (and everything in it) is cleaned up right after.
    with tempfile.TemporaryDirectory(prefix="notes_pdf_math_") as media_dir:
        counter = itertools.count()
        title_markup = _inline_to_markup(title, styles["DocTitle"].fontSize, media_dir, counter)
        story = [Paragraph(title_markup, styles["DocTitle"]), Spacer(1, 10)]
        story.extend(_blocks_to_story(_parse_blocks(notes_markdown), styles, media_dir, counter))

        doc.build(story)

    return buffer.getvalue()

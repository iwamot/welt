"""Pure logic for reading what a Slack message shows, as Markdown.

A message says what it says in three shapes, and all three are read here:
its **blocks**, the **attachments** an app that predates them hangs off it,
and the **mrkdwn** strings inside both. What is left out is the `text`
field, which for anything drawn rather than typed is a summary Slack
writes — headings flattened into the paragraph below them, tables gone
entirely, nested numbering restarted, a locale-dependent notice appended
where the message holds a widget.

Every block that carries words is read: `rich_text`, `header` and `table`
from Slack's Markdown renderer, `markdown`, `section` and `context` from
what apps post — the last as the aside Slack draws it as — `image` and
`video` for what they show, `divider` for the
rule it draws, and `plan` (or the `task_card` Slack drew before it) for
the tools a reply used and how each of them ended. The widgets are read
as well — the buttons a message offers and the field it asks to be typed
in — since a widget still drawn is one still waiting to be used; a widget
that has been used is gone from the message by then. They are named as
what they are and not as words anyone said. A `file` reference carries
nothing at all.

Nothing here knows about Welt: it reads Slack and writes Markdown.
"""

from __future__ import annotations

import re

# The Markdown wrappers for a text run's styles, innermost first.
_STYLE_MARKERS = (("code", "`"), ("italic", "*"), ("bold", "**"), ("strike", "~~"))

# What a list indents by per level. Four spaces put a nested item past the
# marker of the item above it whether that marker is a bullet or a number,
# which is what Markdown reads as nesting rather than as a list of its own.
_LIST_INDENT = "    "

# The deepest heading Markdown gives a level to.
_MAX_HEADING_LEVEL = 6

# A link as mrkdwn writes it: <url> or <url|label>. Only a scheme Slack
# linkifies is matched, so a mention (<@U0123>) is left where it is.
_MRKDWN_LINK = re.compile(r"<((?:https?|mailto):[^|>\s]+)(?:\|([^>]*))?>")


def blocks_to_markdown(blocks: object) -> str:
    """
    Read a message's blocks as the Markdown they were rendered from.

    Args:
        blocks (object): A message's `blocks`, as Slack returns them.

    Returns:
        str: The message as Markdown, blocks separated by a blank line;
            empty when nothing in it carries text.
    """
    if not isinstance(blocks, list):
        return ""
    rendered = (_block_markdown(block) for block in blocks)
    return "\n\n".join(text for text in (part.strip("\n") for part in rendered) if text)


def _block_markdown(block: object) -> str:
    """
    Read one block as Markdown.

    Args:
        block (object): One block of a message.

    Returns:
        str: The block's Markdown, empty for a block carrying no text.
    """
    if not isinstance(block, dict):
        return ""
    match block.get("type"):
        case "header":
            return _header_markdown(block)
        case "rich_text":
            return _rich_text_markdown(block.get("elements"))
        case "table":
            return _table_markdown(block.get("rows"))
        case "divider":
            return "---"
        case "markdown":
            text = block.get("text")
            return text if isinstance(text, str) else ""
        case "section":
            return _section_markdown(block)
        case "image":
            return _image_markdown(block)
        case "video":
            return _video_markdown(block)
        case "context":
            return _context_markdown(block.get("elements"))
        case "plan":
            return _tasks_markdown(block.get("tasks"))
        case "actions":
            return _actions_markdown(block.get("elements"))
        case "input":
            return _input_markdown(block)
        case "task_card":
            return _task_markdown(block)
        case _:
            return ""


def _header_markdown(block: dict) -> str:
    """
    Read a header block as an ATX heading.

    Slack keeps the level a `#`, `##` or `###` was written at, so the
    heading comes back at the depth it went out.

    Args:
        block (dict): A header block.

    Returns:
        str: The heading, empty when it carries no text.
    """
    text = block.get("text")
    title = text.get("text") if isinstance(text, dict) else None
    if not isinstance(title, str) or not title:
        return ""
    level = block.get("level")
    if not isinstance(level, int) or not 1 <= level <= _MAX_HEADING_LEVEL:
        level = 1
    return f"{'#' * level} {title}"


def _context_markdown(elements: object) -> str:
    """
    Read a context block as the aside it is drawn as.

    Slack draws a context line small and grey, under what the message
    says rather than as part of it — a receipt naming who answered, a
    notice about what went wrong. Marking it keeps it from reading as
    something the sender said.

    Args:
        elements (object): The context block's elements.

    Returns:
        str: The elements' text as one bracketed line, empty when none of
            them carries any.
    """
    if not isinstance(elements, list):
        return ""
    texts = [_text_object_markdown(element) for element in elements]
    shown = " ".join(text for text in texts if text)
    return f"[context: {shown}]" if shown else ""


def _text_object_markdown(text_object: object) -> str:
    """
    Read a text object as Markdown.

    A `mrkdwn` object holds what was posted, in Slack's own flavour, so its
    emphasis and its links are written back as Markdown — the same two
    things `rich_text` carries structurally. A `plain_text` one is read as
    it stands.

    Args:
        text_object (object): A block's text object.

    Returns:
        str: The object's text, empty when it carries none.
    """
    if not isinstance(text_object, dict):
        return ""
    text = text_object.get("text")
    if not isinstance(text, str) or not text:
        return ""
    if text_object.get("type") == "mrkdwn":
        return mrkdwn_to_markdown(text)
    return text


def mrkdwn_to_markdown(text: str) -> str:
    """
    Read a mrkdwn string as Markdown.

    Args:
        text (str): A string in Slack's own flavour.

    Returns:
        str: The same string as Markdown.
    """
    # Links first: the angle brackets around one are Slack's own, while a
    # bracket someone typed arrives escaped and is only unescaped after.
    return slack_to_markdown(unescape_slack_formatting(_links_markdown(text)))


def _links_markdown(text: str) -> str:
    """
    Write mrkdwn's links back as Markdown.

    Args:
        text (str): A mrkdwn string.

    Returns:
        str: The string, its links written as Markdown — a labelled one as
            `[label](url)`, a bare one as the URL it already reads as.
    """
    return _MRKDWN_LINK.sub(
        lambda match: f"[{match[2]}]({match[1]})" if match[2] else match[1], text
    )


def _actions_markdown(elements: object) -> str:
    """
    Read an actions block as the widgets it draws.

    Buttons are named together, in the order they are offered, since what
    they are is one choice; anything else that names itself — a menu, a
    date picker — is named on a line of its own.

    Args:
        elements (object): The actions block's elements.

    Returns:
        str: The widgets as bracketed notes, empty when none names itself.
    """
    if not isinstance(elements, list):
        return ""
    buttons: list[str] = []
    others: list[str] = []
    for element in elements:
        if not isinstance(element, dict):
            continue
        if element.get("type") == "button":
            label = _widget_label(element)
            if label:
                buttons.append(label)
            continue
        shown = _widget_markdown(element)
        if shown:
            others.append(shown)
    lines = [f"[buttons: {' | '.join(buttons)}]"] if buttons else []
    return "\n".join(lines + others)


def _widget_markdown(element: object) -> str:
    """
    Read one widget on its own, as what it lets a reader do.

    Args:
        element (object): A widget — an actions element, or the accessory
            a section hangs on its right.

    Returns:
        str: The widget as a bracketed note, empty when it names nothing.
    """
    if not isinstance(element, dict):
        return ""
    if element.get("type") == "image":
        return _image_markdown(element)
    label = _widget_label(element)
    if not label:
        return ""
    return (
        f"[buttons: {label}]" if element.get("type") == "button" else f"[menu: {label}]"
    )


def _input_markdown(block: dict) -> str:
    """
    Read an input block as the field it asks to be filled in.

    Args:
        block (dict): An input block.

    Returns:
        str: The field under its label, or under what its element prompts
            for; empty when it says neither.
    """
    label = _text_object_markdown(block.get("label"))
    element = block.get("element")
    if not label and isinstance(element, dict):
        label = _widget_label(element)
    return f"[input: {label}]" if label else ""


def _widget_label(element: dict) -> str:
    """
    Read what a widget calls itself.

    A block element names itself in a text object; an attachment's own
    button — the shape apps used before Block Kit — names itself in a
    plain string. A widget with nothing to show for a label falls back to
    what it prompts for.

    Args:
        element (dict): One widget.

    Returns:
        str: The widget's label, empty when it has none.
    """
    text = element.get("text")
    if isinstance(text, str) and text:
        return mrkdwn_to_markdown(text)
    return _text_object_markdown(text) or _text_object_markdown(
        element.get("placeholder")
    )


def _tasks_markdown(tasks: object) -> str:
    """
    Read a plan block's tasks as the tools the reply used.

    A task's title is written as it stands — Welt's own reads `Using
    <tool>`, from the `task_update` chunk it streamed, and another app's
    reads whatever that app streamed. Stripping the `Using ` off Welt's own
    would be a rule that fits one writer's phrasing and mangles every
    other. The plan's own title is not read: Slack writes that one, in the
    reader's language.

    What a tool was given and what it returned are not here — the thread
    never showed them — so neither reaches the model.

    Args:
        tasks (object): The plan block's `tasks`.

    Returns:
        str: One task per line, an unfinished or failed one saying so.
    """
    if not isinstance(tasks, list):
        return ""
    lines = (_task_markdown(task) for task in tasks)
    return "\n".join(line for line in lines if line)


def _task_markdown(task: object) -> str:
    """
    Read one task as the tool it names.

    A `plan` holds its tasks in a list; a `task_card` — how Slack drew the
    same `task_update` chunks before — is one task, flat in the block.
    Both reach here.

    Args:
        task (object): One task, or a task card.

    Returns:
        str: The tool as a bracketed note, empty when the task names none.
    """
    if not isinstance(task, dict):
        return ""
    title = task.get("title")
    if not isinstance(title, str) or not title:
        return ""
    status = task.get("status")
    if isinstance(status, str) and status and status != "complete":
        return f"[task: {title} — {status.replace('_', ' ')}]"
    return f"[task: {title}]"


def _section_markdown(block: dict) -> str:
    """
    Read a section block as its text and its fields.

    Args:
        block (dict): A section block.

    Returns:
        str: The block's text, its fields on lines of their own, and the
            widget it hangs on the right; empty when it carries none.
    """
    texts = [_text_object_markdown(block.get("text"))]
    fields = block.get("fields")
    if isinstance(fields, list):
        texts.extend(_text_object_markdown(field) for field in fields)
    texts.append(_widget_markdown(block.get("accessory")))
    return "\n".join(text for text in texts if text)


def _image_markdown(block: dict) -> str:
    """
    Read an image block as the picture it shows.

    An image hosted somewhere reachable is written as Markdown so the URL
    travels with it; one held in Slack is named instead, since its URL
    needs the workspace's credentials to open.

    Args:
        block (dict): An image block.

    Returns:
        str: The image's title and the image itself.
    """
    lines = []
    title = _text_object_markdown(block.get("title"))
    if title:
        lines.append(title)
    alt = block.get("alt_text")
    alt = alt if isinstance(alt, str) else ""
    url = block.get("image_url")
    if isinstance(url, str) and url:
        lines.append(f"![{alt}]({url})")
    elif alt:
        lines.append(f"[image: {alt}]")
    return "\n".join(lines)


def _video_markdown(block: dict) -> str:
    """
    Read a video block as the video it embeds.

    Args:
        block (dict): A video block.

    Returns:
        str: The video, named and linked where it says where it is, with
            its description under it.
    """
    title = _text_object_markdown(block.get("title")) or "video"
    url = block.get("title_url") or block.get("video_url")
    named = f"[video: {title}]"
    lines = [f"{named}({url})" if isinstance(url, str) and url else named]
    description = _text_object_markdown(block.get("description"))
    if description:
        lines.append(description)
    return "\n".join(lines)


def _rich_text_markdown(elements: object) -> str:
    """
    Read a rich text block as Markdown.

    Its elements are a flat sequence, and the paragraph sections carry the
    newlines between them, so the parts are written out one after another
    rather than joined. Not always, though: a paragraph running straight
    into a fence ends without one (observed in a real thread), so anything
    that has to start its own line is given one. A list is a run of sibling
    elements of its own — one per indent level, deeper items sitting
    between the shallower ones they belong under — which is why numbering
    is counted across them here and not inside a single element.

    Args:
        elements (object): The rich text block's elements.

    Returns:
        str: The elements' Markdown.
    """
    if not isinstance(elements, list):
        return ""
    parts: list[str] = []
    numbering: dict[int, tuple[str, int]] = {}
    for element in elements:
        if not isinstance(element, dict):
            continue
        kind = element.get("type")
        if kind == "rich_text_list":
            _start_line(parts)
            parts.append(_list_markdown(element, numbering))
            continue
        # Anything else ends the run, so the next list starts counting again.
        numbering.clear()
        match kind:
            case "rich_text_section":
                parts.append(_inline_markdown(element.get("elements")))
            case "rich_text_quote":
                _start_line(parts)
                parts.append(_quote_markdown(element.get("elements")))
            case "rich_text_preformatted":
                _start_line(parts)
                parts.append(_preformatted_markdown(element))
    return "".join(parts)


def _start_line(parts: list[str]) -> None:
    """
    Break the line, where what has been written has not broken it already.

    Args:
        parts (list[str]): The block's Markdown so far, extended with a
            newline where the next element needs a line of its own.
    """
    for part in reversed(parts):
        if part:
            if not part.endswith("\n"):
                parts.append("\n")
            return


def _list_markdown(element: dict, numbering: dict[int, tuple[str, int]]) -> str:
    """
    Read one list element as its items' lines.

    A list that does not begin at one says where it begins, and is
    numbered from there; one that says nothing continues the run above it,
    which is how Slack writes the part of a list that follows a nested one.

    Args:
        element (dict): A `rich_text_list` element.
        numbering (dict[int, tuple[str, int]]): The style and last number
            reached at each indent level, read and extended here. Levels
            deeper than this element's are forgotten, so a nested list
            that comes back later starts from one again.

    Returns:
        str: One line per item, each ending in a newline.
    """
    indent = element.get("indent")
    if not isinstance(indent, int) or indent < 0:
        indent = 0
    style = element.get("style")
    if style != "ordered":
        style = "bullet"
    for deeper in [level for level in numbering if level > indent]:
        del numbering[deeper]
    start = _list_start(element)
    if start is not None:
        number = start - 1
    else:
        reached = numbering.get(indent)
        number = reached[1] if reached is not None and reached[0] == style else 0
    items = element.get("elements")
    lines: list[str] = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        number += 1
        marker = f"{number}. " if style == "ordered" else "- "
        prefix = f"{_LIST_INDENT * indent}{marker}"
        body = _inline_markdown(item.get("elements")).strip("\n")
        lines.append(_with_prefix(prefix, body))
    numbering[indent] = (style, number)
    return "".join(f"{line}\n" for line in lines)


def _list_start(element: dict) -> int | None:
    """
    Say what number a list asks to be counted from.

    Slack writes `start` on a list that does not begin at one, alongside
    an `offset` one less than it; a list beginning at one says neither
    (measured 2026-08-29, on lists written `1.`, `10.` and `0.`, nested
    and not). Reading `start` is reading both.

    Args:
        element (dict): A `rich_text_list` element.

    Returns:
        int | None: The first number, or None where the list does not say.
    """
    start = element.get("start")
    if isinstance(start, int) and not isinstance(start, bool) and start >= 1:
        return start
    return None


def _with_prefix(prefix: str, body: str) -> str:
    """
    Put a list marker on a body, aligning any lines under the first.

    Args:
        prefix (str): The indent and marker the first line takes.
        body (str): The item's text.

    Returns:
        str: The item as it is written in Markdown.
    """
    first, *rest = body.split("\n")
    padding = " " * len(prefix)
    return "\n".join([f"{prefix}{first}", *(f"{padding}{line}" for line in rest)])


def _quote_markdown(elements: object) -> str:
    """
    Read a quote element as quoted lines.

    Args:
        elements (object): The quote's inline elements.

    Returns:
        str: The quote, each of its lines marked, ending in a newline.
    """
    body = _inline_markdown(elements).strip("\n")
    if not body:
        return ""
    quoted = "\n".join(f"> {line}" for line in body.split("\n"))
    return f"{quoted}\n"


def _preformatted_markdown(element: dict) -> str:
    """
    Read a preformatted element as a fenced code block.

    Slack keeps the language a fence was opened with, and leaves it off a
    fence opened without one. Styling is not read inside a fence: what is
    in it is code, and its characters stand for themselves.

    Args:
        element (dict): A `rich_text_preformatted` element.

    Returns:
        str: The fenced block, ending in a newline.
    """
    language = element.get("language")
    opening = f"```{language}" if isinstance(language, str) and language else "```"
    body = _inline_markdown(element.get("elements"), as_markdown=False).strip("\n")
    return f"{opening}\n{body}\n```\n"


def _table_markdown(rows: object) -> str:
    """
    Read a table block as a Markdown table.

    Slack marks no row as the header, so the first one is taken for it —
    which is where a Markdown table's header row was when Slack rendered it.

    Args:
        rows (object): The table's rows, each a list of cells.

    Returns:
        str: The table, empty when no row holds a cell.
    """
    if not isinstance(rows, list):
        return ""
    lines: list[str] = []
    for row in rows:
        if not isinstance(row, list) or not row:
            continue
        cells = [_cell_markdown(cell) for cell in row]
        lines.append(f"| {' | '.join(cells)} |")
        if len(lines) == 1:
            lines.append(f"| {' | '.join('---' for _ in cells)} |")
    return "\n".join(lines)


def _cell_markdown(cell: object) -> str:
    """
    Read one table cell as the text of a single row.

    A Markdown table's cell cannot hold a line break or an unescaped pipe,
    so a cell holding either is written as one that can be read back.

    Args:
        cell (object): One cell, itself a rich text block.

    Returns:
        str: The cell's text.
    """
    if not isinstance(cell, dict):
        return ""
    text = _rich_text_markdown(cell.get("elements"))
    return " ".join(text.split("\n")).replace("|", "\\|").strip()


def _inline_markdown(elements: object, *, as_markdown: bool = True) -> str:
    """
    Read a run of inline elements as Markdown.

    Args:
        elements (object): The inline elements of a section, quote, list
            item, or preformatted span.
        as_markdown (bool): Whether to write the run's styling and links
            back as Markdown, or leave the characters as they stand.

    Returns:
        str: The run's text.
    """
    if not isinstance(elements, list):
        return ""
    return "".join(
        _inline_element(element, as_markdown=as_markdown)
        for element in elements
        if isinstance(element, dict)
    )


def _inline_element(element: dict, *, as_markdown: bool) -> str:
    """
    Read one inline element as Markdown.

    A link Slack marks `truncated` carries a shortened form for display in
    its text, so the URL is written instead — which is what a bare URL in
    the Markdown was to begin with. A mention, a channel and an emoji are
    written back in the source form the renderer took them from, since
    that is the form the next reply will arrive in too.

    Args:
        element (dict): One inline element.
        as_markdown (bool): Whether to write styling and links back as
            Markdown.

    Returns:
        str: The element's text, empty for an element carrying none.
    """
    style = element.get("style")
    style = style if isinstance(style, dict) else {}
    match element.get("type"):
        case "text":
            text = element.get("text")
            if not isinstance(text, str):
                return ""
            return _styled(text, style) if as_markdown else text
        case "link":
            return _link_markdown(element, as_markdown=as_markdown)
        case "user":
            return _reference("@", element.get("user_id"))
        case "channel":
            return _reference("#", element.get("channel_id"))
        case "usergroup":
            return _reference("!subteam^", element.get("usergroup_id"))
        case "broadcast":
            return _reference("!", element.get("range"))
        case "emoji":
            name = element.get("name")
            return f":{name}:" if isinstance(name, str) and name else ""
        case "date":
            fallback = element.get("fallback")
            return fallback if isinstance(fallback, str) else ""
        case _:
            return ""


def _reference(sigil: str, identifier: object) -> str:
    """
    Write a mention back in the form Slack reads it in.

    Args:
        sigil (str): What marks the kind of reference (`@`, `#`, `!`).
        identifier (object): The referenced ID or range.

    Returns:
        str: The reference, empty when the element names nothing.
    """
    if not isinstance(identifier, str) or not identifier:
        return ""
    return f"<{sigil}{identifier}>"


def _link_markdown(element: dict, *, as_markdown: bool) -> str:
    """
    Read a link element as Markdown.

    Args:
        element (dict): A link element.
        as_markdown (bool): Whether to write the link back as Markdown, or
            as the bare URL it stands for.

    Returns:
        str: The link, as a labelled one only where it has a label of its
            own to carry.
    """
    url = element.get("url")
    label = element.get("text")
    if not isinstance(url, str) or not url:
        return label if isinstance(label, str) else ""
    if (
        not as_markdown
        or not isinstance(label, str)
        or not label
        or label == url
        or element.get("truncated") is True
    ):
        return url
    style = element.get("style")
    style = style if isinstance(style, dict) else {}
    return f"[{_styled(label, style)}]({url})"


def _styled(text: str, style: dict) -> str:
    """
    Write a text run's styles back as Markdown.

    The markers go inside the run's own surrounding spaces, since Markdown
    reads emphasis by what sits against the marker.

    Args:
        text (str): The run's text.
        style (dict): The run's styles, as Slack marks them.

    Returns:
        str: The run, wrapped in the markers for the styles it carries.
    """
    markers = [marker for name, marker in _STYLE_MARKERS if style.get(name)]
    core = text.strip()
    if not markers or not core:
        return text
    leading = text[: len(text) - len(text.lstrip())]
    trailing = text[len(text.rstrip()) :]
    for marker in markers:
        core = f"{marker}{core}{marker}"
    return f"{leading}{core}{trailing}"


def attachments_to_markdown(attachments: object) -> str:
    """
    Read a message's attachments as the Markdown they show.

    Attachments are what an app hung off a message before Block Kit, and
    plenty of apps still post them — a GitHub workflow notification is one
    message carrying nothing but an attachment. Their strings are mrkdwn,
    including the fields Slack does not list in `mrkdwn_in`: an app writes
    a title as `<url|label>` whether or not it says the field is mrkdwn.

    Args:
        attachments (object): A message's `attachments`, as Slack returns
            them.

    Returns:
        str: The attachments as Markdown, each separated by a blank line.
    """
    if not isinstance(attachments, list):
        return ""
    rendered = (_attachment_markdown(a) for a in attachments if isinstance(a, dict))
    return "\n\n".join(text for text in rendered if text)


def _attachment_markdown(attachment: dict) -> str:
    """
    Read one attachment, in the order it is shown.

    Its buttons are read last, where they are drawn. `fallback` stands in
    only where nothing else carries anything, which is what it is for.

    Args:
        attachment (dict): One attachment.

    Returns:
        str: The attachment as Markdown.
    """
    parts = [
        _attachment_string(attachment, "pretext"),
        _linked(
            _attachment_string(attachment, "author_name"),
            attachment.get("author_link"),
        ),
        _linked(_attachment_string(attachment, "title"), attachment.get("title_link")),
        _attachment_string(attachment, "text"),
        _attachment_fields_markdown(attachment.get("fields")),
        _attachment_image_markdown(attachment),
        _attachment_string(attachment, "footer"),
        _actions_markdown(attachment.get("actions")),
    ]
    shown = "\n\n".join(part for part in parts if part)
    return shown or _attachment_string(attachment, "fallback")


def _attachment_image_markdown(attachment: dict) -> str:
    """
    Read the picture an attachment shows.

    An attachment names no alt text for its image, so the URL is all
    there is to say about it.

    Args:
        attachment (dict): One attachment.

    Returns:
        str: The image as a bracketed note, empty when it shows none.
    """
    url = attachment.get("image_url") or attachment.get("thumb_url")
    return f"[image: {url}]" if isinstance(url, str) and url else ""


def _attachment_fields_markdown(fields: object) -> str:
    """
    Read an attachment's fields as their titles and values.

    Args:
        fields (object): The attachment's `fields`.

    Returns:
        str: One field per paragraph, its title above its value.
    """
    if not isinstance(fields, list):
        return ""
    rendered = []
    for field in fields:
        if not isinstance(field, dict):
            continue
        lines = [
            _attachment_string(field, "title"),
            _attachment_string(field, "value"),
        ]
        shown = "\n".join(line for line in lines if line)
        if shown:
            rendered.append(shown)
    return "\n\n".join(rendered)


def _attachment_string(attachment: dict, key: str) -> str:
    """
    Read one of an attachment's strings as Markdown.

    Args:
        attachment (dict): An attachment, or one of its fields.
        key (str): The string to read.

    Returns:
        str: The string as Markdown, empty when it holds none.
    """
    value = attachment.get(key)
    return mrkdwn_to_markdown(value) if isinstance(value, str) and value else ""


def _linked(text: str, url: object) -> str:
    """
    Link a line where the attachment says where it points.

    Args:
        text (str): The line's text, already Markdown.
        url (object): The URL the attachment gives for it.

    Returns:
        str: The line, linked only where both halves are there and the
            text is not a link already.
    """
    if not text or not isinstance(url, str) or not url or text.startswith("["):
        return text
    return f"[{text}]({url})"


def unescape_slack_formatting(content: str) -> str:
    """
    Unescape Slack formatting characters.

    Unescape &, < and >, since Slack replaces these with their HTML equivalents.
    See also: https://api.slack.com/reference/surfaces/formatting#escaping

    Args:
        content (str): The input string containing Slack formatting.

    Returns:
        str: The unescaped string.
    """
    return content.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")


def slack_to_markdown(content: str) -> str:
    """
    Convert Slack mrkdwn to Markdown format.

    Only spans that Slack itself renders as formatting are converted: a marker
    adjacent to an ASCII letter, digit, or another marker of the same kind is
    literal text in Slack's renderer, so `snake_case_names` and `2*3*4` pass
    through unchanged (CJK-adjacent markers do format, matching Slack).
    See also: https://api.slack.com/reference/surfaces/formatting#basics

    Args:
        content (str): The input string in Slack mrkdwn format.

    Returns:
        str: The converted string in Markdown format.
    """
    # Split the input string into parts based on code blocks and inline code
    parts = re.split(r"(?s)(```.+?```|`[^`\n]+?`)", content)

    # Apply the bold, italic, and strikethrough formatting to text not within code
    result = ""
    for part in parts:
        if not part.startswith("```") and not part.startswith("`"):
            for o, n in [
                # *bold* to **bold**
                (
                    r"(?<![A-Za-z0-9*])\*(?!\s)([^\*\n]+?)(?<!\s)\*(?![A-Za-z0-9*])",
                    r"**\1**",
                ),
                # _italic_ to *italic*
                (
                    r"(?<![A-Za-z0-9_])_(?!\s)([^_\n]+?)(?<!\s)_(?![A-Za-z0-9_])",
                    r"*\1*",
                ),
                # ~strike~ to ~~strike~~
                (
                    r"(?<![A-Za-z0-9~])~(?!\s)([^~\n]+?)(?<!\s)~(?![A-Za-z0-9~])",
                    r"~~\1~~",
                ),
            ]:
                part = re.sub(o, n, part)
        result += part
    return result

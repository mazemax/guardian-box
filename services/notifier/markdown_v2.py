"""
Telegram MarkdownV2 escaping and validation.

https://core.telegram.org/bots/api#markdownv2-style
"""

import re
from typing import List

MARKDOWN_V2_SPECIAL_RE = re.compile(r"([_*\[\]()~`>#+\-=|{}.!\\])")


def escape_markdown_v2(text: str) -> str:
    if not text:
        return text
    return MARKDOWN_V2_SPECIAL_RE.sub(r"\\\1", text)


def find_unescaped_markdown_v2_issues(text: str) -> List[str]:
    issues: List[str] = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "\\" and i + 1 < len(text):
            i += 2
            continue
        if MARKDOWN_V2_SPECIAL_RE.match(ch):
            issues.append(f"unescaped '{ch}' at index {i}")
        i += 1
    return issues


def validate_markdown_v2_literals(text: str) -> List[str]:
    return find_unescaped_markdown_v2_issues(text)


def validate_markdown_v2_with_bold_spans(text: str) -> List[str]:
    issues: List[str] = []
    parts = text.split("*")
    if len(parts) < 3:
        return validate_markdown_v2_literals(text)
    for idx, segment in enumerate(parts):
        issues.extend(validate_markdown_v2_literals(segment))
    return issues

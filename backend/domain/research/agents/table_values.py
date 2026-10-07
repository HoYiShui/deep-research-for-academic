"""Literal whole-cell view, not a table repair or inferred numeric conversion."""

import re
from html.parser import HTMLParser


def normalized(value):
    return " ".join(value.split())


class _Cells(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.values, self.parts, self.kind = [], [], None

    def handle_starttag(self, tag, attrs):
        if tag in {"td", "th"}:
            if self.kind is not None:
                raise ValueError("Malformed or nested original table cell")
            self.kind, self.parts = tag, []
        elif self.kind and tag in {"sup", "sub"}:
            # Never concatenate 10<sup>18</sup> into the invented integer 1018.
            self.parts.append("^" if tag == "sup" else "_")
        elif self.kind and tag == "br":
            self.parts.append("\n")
        elif self.kind and tag in {"script", "style", "table"}:
            raise ValueError("Unsupported active or nested table content")

    def handle_endtag(self, tag):
        if tag in {"td", "th"}:
            if self.kind != tag:
                raise ValueError("Malformed original table cell boundaries")
            self.values.append(normalized("".join(self.parts)))
            self.kind, self.parts = None, []

    def handle_data(self, data):
        if self.kind:
            self.parts.append(data)


def cell_values(content):
    if re.search(r"<table(?:\s|>)", content, re.IGNORECASE):
        parser = _Cells()
        parser.feed(content)
        parser.close()
        if parser.kind is not None or not parser.values:
            raise ValueError("Original table has no complete cells")
        return parser.values
    # Controlled/plain pipe tables are also supported; no escaping or column
    # repair is guessed. Other representations must fail closed for observations.
    rows = [line for line in content.splitlines() if "|" in line]
    if not rows:
        raise ValueError("Original table has no supported cell representation")
    return [normalized(value) for row in rows for value in row.strip("|").split("|")]

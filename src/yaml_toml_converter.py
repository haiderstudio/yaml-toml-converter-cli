#!/usr/bin/env python3
"""Convert between YAML and TOML, with a round-trip check.

Run: python3 src/yaml_toml_converter.py config.yaml
     python3 src/yaml_toml_converter.py config.toml
     python3 src/yaml_toml_converter.py --to toml config.yaml
     python3 src/yaml_toml_converter.py --check config.yaml config.toml
     cat config.yaml | python3 src/yaml_toml_converter.py --from yaml --to toml
     python3 src/yaml_toml_converter.py --output config.toml config.yaml

The tool converts one file between YAML and TOML, both ways. It reads
TOML with Python's standard tomllib module and writes TOML itself, and
it reads and writes a documented subset of YAML with its own small
standard-library parser (see "Supported YAML subset" in the README).
Every conversion is checked by re-reading the output and comparing it
with the input data; a conversion that does not round-trip is reported
and nothing is written.

It refuses lossy conversion instead of guessing: a YAML null value has
no TOML form, a TOML date has no YAML form here, a non-mapping top
level has no TOML form, and YAML anchors, aliases, tags, and merge
keys are outside the supported subset. A refusal reports the exact
reason and writes nothing.

Exit codes: 0 when the conversion (or check) succeeds, 1 when any
file is bad, refused, or fails the round-trip check, 2 for a usage
problem (missing file, unknown format, bad flag combination).

Python standard library only.
"""

import argparse
import datetime
import math
import re
import sys
import tomllib
from pathlib import Path

VERSION = "1.0.0"


class ConvertError(Exception):
    """A conversion failure: bad input, a refused feature, or a failed check.

    Carries an optional line number so the report can point at it.
    """

    def __init__(self, message, lineno=None):
        super().__init__(message)
        self.message = message
        self.lineno = lineno


class YamlError(ConvertError):
    """A YAML input the supported subset cannot read."""


# ---------------------------------------------------------------------------
# Plain-scalar handling (YAML 1.2 core schema, the subset this tool reads)
# ---------------------------------------------------------------------------

_INT_RE = re.compile(r"^[-+]?[0-9]+(_[0-9]+)*$")
_HEX_RE = re.compile(r"^0x[0-9a-fA-F]+(_[0-9a-fA-F]+)*$")
_OCT_RE = re.compile(r"^0o[0-7]+(_[0-7]+)*$")
_FLOAT_RE = re.compile(
    r"^[-+]?(\.[0-9]+|[0-9]+(\.[0-9]*)?)([eE][-+]?[0-9]+)?$"
)
_BARE_KEY_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def resolve_plain(text):
    """Resolve a plain YAML scalar to a Python value (YAML 1.2 core schema).

    Empty, ~ and null become None; true/false become booleans; numbers
    become int or float; anything else stays a string. Note that yes/no
    and on/off are plain strings here, not booleans.
    """
    if text == "" or text in ("~", "null", "Null", "NULL"):
        return None
    if text in ("true", "True", "TRUE"):
        return True
    if text in ("false", "False", "FALSE"):
        return False
    if _INT_RE.match(text):
        return int(text.replace("_", ""))
    if _HEX_RE.match(text):
        return int(text.replace("_", ""), 16)
    if _OCT_RE.match(text):
        return int(text.replace("_", ""), 8)
    if text in (".inf", ".Inf", ".INF", "+.inf", "+.Inf", "+.INF"):
        return math.inf
    if text in ("-.inf", "-.Inf", "-.INF"):
        return -math.inf
    if text in (".nan", ".NaN", ".NAN"):
        return math.nan
    if _FLOAT_RE.match(text) and ("." in text or "e" in text or "E" in text):
        return float(text.replace("_", ""))
    return text


def _has_tab_indent(raw):
    """True when the leading whitespace of a line contains a tab."""
    stripped = raw.lstrip(" \t")
    return "\t" in raw[:len(raw) - len(stripped)]


def _strip_comment(line):
    """Remove a # comment from one line, keeping # inside quotes.

    A # starts a comment only at the start of the line or after a
    space or tab, and only outside single and double quotes.
    """
    in_single = False
    in_double = False
    i = 0
    while i < len(line):
        ch = line[i]
        if in_single:
            if ch == "'":
                if i + 1 < len(line) and line[i + 1] == "'":
                    i += 2
                    continue
                in_single = False
        elif in_double:
            if ch == "\\":
                i += 2
                continue
            if ch == '"':
                in_double = False
        else:
            if ch == "'":
                in_single = True
            elif ch == '"':
                in_double = True
            elif ch == "#" and (i == 0 or line[i - 1] in " \t"):
                return line[:i].rstrip()
        i += 1
    return line.rstrip()


_DOUBLE_ESCAPES = {
    "0": "\0",
    "a": "\a",
    "b": "\b",
    "t": "\t",
    "\t": "\t",
    "n": "\n",
    "v": "\v",
    "f": "\f",
    "r": "\r",
    "e": "\x1b",
    '"': '"',
    "\\": "\\",
    "/": "/",
    " ": " ",
    "N": "\x85",
    "_": "\xa0",
    "L": "\u2028",
    "P": "\u2029",
}


def _parse_double_quoted(text, lineno):
    """Parse a double-quoted scalar. Returns (value, rest_after_quote)."""
    out = []
    i = 1
    while i < len(text):
        ch = text[i]
        if ch == '"':
            return "".join(out), text[i + 1:]
        if ch == "\\":
            if i + 1 >= len(text):
                raise YamlError("bad escape at end of quoted string", lineno)
            esc = text[i + 1]
            if esc in _DOUBLE_ESCAPES:
                out.append(_DOUBLE_ESCAPES[esc])
                i += 2
            elif esc == "x":
                out.append(chr(int(text[i + 2:i + 4], 16)))
                i += 4
            elif esc == "u":
                out.append(chr(int(text[i + 2:i + 6], 16)))
                i += 6
            elif esc == "U":
                out.append(chr(int(text[i + 2:i + 10], 16)))
                i += 10
            else:
                raise YamlError(
                    "unknown escape '\\%s' in double-quoted string" % esc,
                    lineno,
                )
        else:
            out.append(ch)
            i += 1
    raise YamlError("unclosed double-quoted string", lineno)


def _parse_single_quoted(text, lineno):
    """Parse a single-quoted scalar. Returns (value, rest_after_quote)."""
    out = []
    i = 1
    while i < len(text):
        ch = text[i]
        if ch == "'":
            if i + 1 < len(text) and text[i + 1] == "'":
                out.append("'")
                i += 2
                continue
            return "".join(out), text[i + 1:]
        out.append(ch)
        i += 1
    raise YamlError("unclosed single-quoted string", lineno)


def _parse_quoted(text, lineno):
    """Parse a quoted scalar that must take up the whole text."""
    if text.startswith('"'):
        value, rest = _parse_double_quoted(text, lineno)
    else:
        value, rest = _parse_single_quoted(text, lineno)
    if rest.strip() != "":
        raise YamlError("unexpected text after quoted string", lineno)
    return value


# ---------------------------------------------------------------------------
# YAML subset parser
# ---------------------------------------------------------------------------


class _Line:
    """One significant (non-blank, non-comment-only) source line."""

    __slots__ = ("no", "indent", "text")

    def __init__(self, no, indent, text):
        self.no = no
        self.indent = indent
        self.text = text


class YamlParser:
    """A parser for the documented YAML subset (see README).

    Block mappings and sequences, flow collections on one line, plain,
    single-quoted and double-quoted scalars, comments, and literal /
    folded block scalars. Anchors, aliases, tags, merge keys, complex
    keys, directives, and multi-document files are refused.
    """

    def __init__(self, text):
        if text.startswith("\ufeff"):
            text = text[1:]
        self.raw = text.splitlines()
        self.sig = []
        for index, raw in enumerate(self.raw):
            if _has_tab_indent(raw):
                raise YamlError(
                    "tabs are not allowed for indentation", index + 1
                )
            stripped = _strip_comment(raw)
            if stripped.strip() == "":
                continue
            indent = len(raw) - len(raw.lstrip(" "))
            self.sig.append(_Line(index + 1, indent, stripped.strip()))
        self.pos = 0

    def peek(self):
        """The next significant line, or None."""
        if self.pos < len(self.sig):
            return self.sig[self.pos]
        return None

    def advance(self):
        self.pos += 1

    def skip_past_physical(self, last_no):
        """Move past every significant line up to a physical line number."""
        while self.pos < len(self.sig) and self.sig[self.pos].no <= last_no:
            self.pos += 1

    def parse(self):
        """Parse the whole document and return the Python value."""
        line = self.peek()
        if line is None:
            return None
        if line.text.startswith("%"):
            raise YamlError(
                "directives (lines starting with %) are not supported",
                line.no,
            )
        if line.text == "---":
            self.advance()
            line = self.peek()
            if line is None:
                return None
        value, _ = self.parse_node(line.indent)
        line = self.peek()
        if line is not None:
            if line.text == "...":
                self.advance()
                line = self.peek()
            elif line.text == "---":
                raise YamlError(
                    "multi-document files are not supported", line.no
                )
            if line is not None:
                raise YamlError(
                    "unexpected content after the document: %r" % line.text,
                    line.no,
                )
        return value

    def parse_node(self, indent, first=None):
        """Parse one node at the given indent. Returns (value, _).

        `first` is a virtual line (already consumed) used when a node
        starts on a sequence dash line, e.g. `- name: x`.
        """
        if first is not None:
            line = first
        else:
            line = self.peek()
            if line is None:
                raise YamlError("unexpected end of file", None)
            self.advance()
        text = line.text
        if text == "-" or text.startswith("- "):
            return self.parse_sequence(indent, first=line)
        if _is_mapping_start(text):
            return self.parse_mapping(indent, first=line)
        return self.parse_inline(text, line.no), None

    def parse_mapping(self, indent, first=None):
        """Parse a block mapping. Returns (dict, _)."""
        result = {}
        use_first = first is not None
        while True:
            if use_first:
                line = first
                use_first = False
            else:
                line = self.peek()
                if line is None:
                    break
                if line.indent < indent:
                    break
                if line.indent > indent:
                    raise YamlError(
                        "bad indentation: line is indented further than "
                        "its mapping",
                        line.no,
                    )
                if line.text == "-" or line.text.startswith("- "):
                    break
                if line.text == "...":
                    break
                self.advance()
            if line.text.startswith("? "):
                raise YamlError(
                    "complex keys (? key) are not supported", line.no
                )
            key_text, rest = _split_mapping_entry(line.text)
            if key_text is None:
                raise YamlError(
                    "expected 'key: value' in mapping, got %r" % line.text,
                    line.no,
                )
            key = self.parse_key(key_text, line.no)
            if any(existing == key for existing in result):
                raise YamlError(
                    "duplicate key %r in mapping" % (key_text.strip(),),
                    line.no,
                )
            if rest == "":
                nxt = self.peek()
                if nxt is not None and nxt.indent > indent:
                    value, _ = self.parse_node(nxt.indent)
                elif nxt is not None and nxt.indent == indent and (
                    nxt.text == "-" or nxt.text.startswith("- ")
                ):
                    value, _ = self.parse_sequence(indent)
                else:
                    value = None
            elif _is_block_scalar(rest):
                value = self.parse_block_scalar(line, indent)
            else:
                value = self.parse_inline(rest, line.no)
            result[key] = value
        return result, None

    def parse_sequence(self, indent, first=None):
        """Parse a block sequence. Returns (list, _)."""
        result = []
        use_first = first is not None
        while True:
            if use_first:
                line = first
                use_first = False
            else:
                line = self.peek()
                if line is None:
                    break
                if line.indent < indent:
                    break
                if line.indent > indent:
                    raise YamlError(
                        "bad indentation: line is indented further than "
                        "its sequence",
                        line.no,
                    )
                if not (line.text == "-" or line.text.startswith("- ")):
                    break
                self.advance()
            rest = line.text[1:].lstrip(" ")
            if rest == "":
                nxt = self.peek()
                if nxt is not None and nxt.indent > indent:
                    value, _ = self.parse_node(nxt.indent)
                else:
                    value = None
            elif _is_block_scalar(rest):
                value = self.parse_block_scalar(line, indent)
            elif rest == "-" or rest.startswith("- "):
                value, _ = self.parse_sequence(
                    indent + 2,
                    first=_Line(line.no, indent + 2, rest),
                )
            elif _is_mapping_start(rest):
                value, _ = self.parse_mapping(
                    indent + 2,
                    first=_Line(line.no, indent + 2, rest),
                )
            else:
                value = self.parse_inline(rest, line.no)
            result.append(value)
        return result, None

    def parse_block_scalar(self, line, parent_indent):
        """Read a literal (|) or folded (>) block scalar.

        Consumes the raw physical lines after `line` that are blank
        or indented further than the parent, then moves the
        significant-line pointer past them.
        """
        indicator = line.text.split()[-1]
        if indicator not in ("|", "|-", "|+", ">", ">-", ">+"):
            raise YamlError(
                "unsupported block scalar %r (use |, |-, |+, >, >- or >+)"
                % indicator,
                line.no,
            )
        style = indicator[0]
        chomping = indicator[1:] if len(indicator) > 1 else ""
        start = line.no  # physical line numbers are 1-based; raw is 0-based
        collected = []
        content_indent = None
        last_no = start
        for index in range(start, len(self.raw)):
            raw = self.raw[index]
            if raw.strip() == "":
                collected.append("")
                last_no = index + 1
                continue
            indent = len(raw) - len(raw.lstrip(" "))
            if _has_tab_indent(raw):
                raise YamlError(
                    "tabs are not allowed for indentation", index + 1
                )
            if indent <= parent_indent:
                break
            if content_indent is None:
                content_indent = indent
            if indent < content_indent:
                raise YamlError(
                    "bad indentation in block scalar", index + 1
                )
            collected.append(raw[content_indent:])
            last_no = index + 1
        # Drop trailing blank lines collected past the real content;
        # chomping decides how many newlines the value keeps.
        while collected and collected[-1] == "":
            collected.pop()
        if style == "|":
            body = "".join(item + "\n" for item in collected)
        else:
            body = _fold_lines(collected)
        if chomping == "-":
            body = body.rstrip("\n")
        elif chomping == "+":
            pass
        else:  # clip: exactly one trailing newline, when there is content
            body = body.rstrip("\n")
            if body:
                body += "\n"
        self.skip_past_physical(last_no)
        return body

    def parse_key(self, key_text, lineno):
        """Parse a mapping key: a plain or quoted scalar, never a flow
        collection and never an anchor, alias, or tag."""
        text = key_text.strip()
        if text == "":
            raise YamlError("empty mapping key", lineno)
        if text[0] in "&*!":
            raise YamlError(
                "anchors, aliases, and tags are not supported", lineno
            )
        if text.startswith("{") or text.startswith("["):
            raise YamlError(
                "mapping keys must be plain scalars, not collections",
                lineno,
            )
        if text.startswith('"') or text.startswith("'"):
            return _parse_quoted(text, lineno)
        if text == "<<" :
            raise YamlError("merge keys (<<) are not supported", lineno)
        return resolve_plain(text)

    def parse_inline(self, text, lineno):
        """Parse one inline value: flow collection, quoted string, or
        plain scalar."""
        text = text.strip()
        if text == "":
            return None
        if text[0] in "&*!":
            raise YamlError(
                "anchors, aliases, and tags are not supported", lineno
            )
        if text.startswith("{") or text.startswith("["):
            value, rest = _parse_flow(text, 0, lineno)
            if rest.strip() != "":
                raise YamlError(
                    "unexpected text after flow collection", lineno
                )
            return value
        if text.startswith('"') or text.startswith("'"):
            return _parse_quoted(text, lineno)
        if ": " in text or text.endswith(":"):
            raise YamlError(
                "plain scalar cannot contain ': ' (%r)" % text, lineno
            )
        return resolve_plain(text)


def _fold_lines(lines):
    """Fold block-scalar lines the way > does: single newlines become
    spaces, blank lines become newlines."""
    out = []
    for index, item in enumerate(lines):
        if item == "":
            out.append("\n")
        else:
            out.append(item)
            if index + 1 < len(lines) and lines[index + 1] != "":
                out.append(" ")
            else:
                out.append("\n")
    return "".join(out)


def _is_block_scalar(rest):
    """True when a mapping value / sequence item is a block scalar."""
    return rest in ("|", "|-", "|+", ">", ">-", ">+")


def _split_mapping_entry(text):
    """Split 'key: value' at the first top-level colon.

    The colon counts only outside quotes and flow collections, and
    only when followed by a space or the end of the line. Returns
    (key_text, rest) or (None, None) when there is no such colon.
    """
    in_single = False
    in_double = False
    depth = 0
    i = 0
    while i < len(text):
        ch = text[i]
        if in_single:
            if ch == "'":
                if i + 1 < len(text) and text[i + 1] == "'":
                    i += 2
                    continue
                in_single = False
        elif in_double:
            if ch == "\\":
                i += 2
                continue
            if ch == '"':
                in_double = False
        else:
            if ch == "'":
                in_single = True
            elif ch == '"':
                in_double = True
            elif ch in "[{":
                depth += 1
            elif ch in "]}":
                depth -= 1
            elif ch == ":" and depth == 0:
                if i + 1 == len(text) or text[i + 1] == " ":
                    return text[:i], text[i + 1:].strip()
        i += 1
    return None, None


def _is_mapping_start(text):
    """True when a line (or dash-line rest) starts a mapping entry."""
    if text.startswith("? "):
        return True
    key_text, _rest = _split_mapping_entry(text)
    return key_text is not None


def _parse_flow(text, pos, lineno):
    """Parse one flow value starting at pos. Returns (value, rest)."""
    while pos < len(text) and text[pos] in " \t":
        pos += 1
    if pos >= len(text):
        raise YamlError("unexpected end of flow collection", lineno)
    ch = text[pos]
    if ch == "{":
        return _parse_flow_mapping(text, pos, lineno)
    if ch == "[":
        return _parse_flow_sequence(text, pos, lineno)
    if ch == '"':
        value, rest = _parse_double_quoted(text[pos:], lineno)
        return value, rest
    if ch == "'":
        value, rest = _parse_single_quoted(text[pos:], lineno)
        return value, rest
    if ch in "&*!":
        raise YamlError(
            "anchors, aliases, and tags are not supported", lineno
        )
    end = pos
    while end < len(text) and text[end] not in ",]}":
        # In a flow mapping a plain scalar key ends at a colon followed
        # by a space (or a closer); a colon inside a value such as a
        # URL (http://x) does not end the scalar.
        if text[end] == ":" and (
            end + 1 >= len(text) or text[end + 1] in " ,]}"
        ):
            break
        end += 1
    token = text[pos:end].strip()
    if token == "":
        raise YamlError("empty value in flow collection", lineno)
    if ": " in token or token.endswith(":"):
        raise YamlError(
            "plain scalar cannot contain ': ' (%r)" % token, lineno
        )
    return resolve_plain(token), text[end:]


def _parse_flow_mapping(text, pos, lineno):
    """Parse {k: v, ...}. Returns (dict, rest_after_brace)."""
    result = {}
    pos += 1
    while True:
        while pos < len(text) and text[pos] in " \t":
            pos += 1
        if pos >= len(text):
            raise YamlError("unclosed flow mapping", lineno)
        if text[pos] == "}":
            return result, text[pos + 1:]
        if text[pos] == ",":
            pos += 1
            continue
        key, rest = _parse_flow(text, pos, lineno)
        pos = len(text) - len(rest)
        while pos < len(text) and text[pos] in " \t":
            pos += 1
        if pos >= len(text) or text[pos] != ":":
            raise YamlError(
                "expected ':' after flow mapping key", lineno
            )
        pos += 1
        value, rest = _parse_flow(text, pos, lineno)
        pos = len(text) - len(rest)
        if isinstance(key, (dict, list)):
            raise YamlError(
                "mapping keys must be plain scalars, not collections",
                lineno,
            )
        if any(existing == key for existing in result):
            raise YamlError("duplicate key %r in mapping" % (key,), lineno)
        result[key] = value
        while pos < len(text) and text[pos] in " \t":
            pos += 1
        if pos < len(text) and text[pos] == ",":
            pos += 1
        elif pos < len(text) and text[pos] == "}":
            continue
        elif pos >= len(text):
            raise YamlError("unclosed flow mapping", lineno)
        else:
            raise YamlError(
                "expected ',' or '}' in flow mapping", lineno
            )


def _parse_flow_sequence(text, pos, lineno):
    """Parse [a, b, ...]. Returns (list, rest_after_bracket)."""
    result = []
    pos += 1
    while True:
        while pos < len(text) and text[pos] in " \t":
            pos += 1
        if pos >= len(text):
            raise YamlError("unclosed flow sequence", lineno)
        if text[pos] == "]":
            return result, text[pos + 1:]
        if text[pos] == ",":
            pos += 1
            continue
        value, rest = _parse_flow(text, pos, lineno)
        pos = len(text) - len(rest)
        result.append(value)
        while pos < len(text) and text[pos] in " \t":
            pos += 1
        if pos < len(text) and text[pos] == ",":
            pos += 1
        elif pos < len(text) and text[pos] == "]":
            continue
        elif pos >= len(text):
            raise YamlError("unclosed flow sequence", lineno)
        else:
            raise YamlError(
                "expected ',' or ']' in flow sequence", lineno
            )


def parse_yaml(text):
    """Parse YAML text in the supported subset. Returns the Python value."""
    return YamlParser(text).parse()


def parse_toml(text):
    """Parse TOML text with the standard library. Returns the Python value."""
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise ConvertError("invalid TOML: %s" % error)


# ---------------------------------------------------------------------------
# Compatibility checks (what each format cannot hold)
# ---------------------------------------------------------------------------


def _path_name(path):
    return ".".join(path) if path else "top level"


def check_toml_compatible(data, path=None):
    """Refuse data TOML cannot hold: null values, non-string keys, and
    a top level that is not a mapping."""
    path = path or []
    if not isinstance(data, dict):
        raise ConvertError(
            "refused: top level must be a mapping for TOML, "
            "got %s" % type(data).__name__
        )
    for key, value in data.items():
        child = path + [str(key)]
        if not isinstance(key, str):
            raise ConvertError(
                "refused: key %r at %s is not a string; TOML keys "
                "must be strings" % (key, _path_name(path))
            )
        if value is None:
            raise ConvertError(
                "refused: null value at %s has no TOML form" % _path_name(child)
            )
        if isinstance(value, (datetime.datetime, datetime.date, datetime.time)):
            raise ConvertError(
                "refused: date/time value at %s has no YAML form here"
                % _path_name(child)
            )
        if isinstance(value, dict):
            check_toml_compatible(value, child)
        elif isinstance(value, list):
            for index, item in enumerate(value):
                check_toml_compatible_list(item, child + [str(index)])


def check_toml_compatible_list(item, path):
    """The list-item half of check_toml_compatible."""
    if item is None:
        raise ConvertError(
            "refused: null value at %s has no TOML form" % _path_name(path)
        )
    if isinstance(item, (datetime.datetime, datetime.date, datetime.time)):
        raise ConvertError(
            "refused: date/time value at %s has no YAML form here"
            % _path_name(path)
        )
    if isinstance(item, dict):
        check_toml_compatible(item, path)
    elif isinstance(item, list):
        for index, sub in enumerate(item):
            check_toml_compatible_list(sub, path + [str(index)])


def check_yaml_compatible(data, path=None):
    """Refuse data the YAML writer cannot hold: date/time values
    (they would come back as plain strings) and non-string keys."""
    path = path or []
    if isinstance(data, (datetime.datetime, datetime.date, datetime.time)):
        raise ConvertError(
            "refused: date/time value at %s has no YAML form here"
            % _path_name(path)
        )
    if isinstance(data, dict):
        for key, value in data.items():
            if not isinstance(key, str):
                raise ConvertError(
                    "refused: key %r at %s is not a string"
                    % (key, _path_name(path))
                )
            check_yaml_compatible(value, path + [str(key)])
    elif isinstance(data, list):
        for index, item in enumerate(data):
            check_yaml_compatible(item, path + [str(index)])


# ---------------------------------------------------------------------------
# TOML writer
# ---------------------------------------------------------------------------


def _toml_key(key):
    if _BARE_KEY_RE.match(key):
        return key
    return _toml_string(key)


def _toml_string(value):
    out = ['"']
    for ch in value:
        if ch == '"':
            out.append('\\"')
        elif ch == "\\":
            out.append("\\\\")
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\t":
            out.append("\\t")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\b":
            out.append("\\b")
        elif ch == "\f":
            out.append("\\f")
        elif ord(ch) < 0x20 or ord(ch) == 0x7F:
            out.append("\\u%04X" % ord(ch))
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def _toml_value(value):
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, int):
        if value < -(2**63) or value > 2**63 - 1:
            raise ConvertError(
                "refused: integer %d is too large for TOML (64-bit)"
                % value
            )
        return str(value)
    if isinstance(value, float):
        if math.isnan(value):
            return "nan"
        if math.isinf(value):
            return "inf" if value > 0 else "-inf"
        return repr(value)
    if isinstance(value, str):
        return _toml_string(value)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    if isinstance(value, dict):
        inner = ", ".join(
            "%s = %s" % (_toml_key(key), _toml_value(item))
            for key, item in value.items()
        )
        return "{" + inner + "}"
    raise ConvertError(
        "refused: value of type %s has no TOML form" % type(value).__name__
    )


def _is_array_of_tables(value):
    return (
        isinstance(value, list)
        and len(value) > 0
        and all(isinstance(item, dict) for item in value)
    )


def _emit_toml_table(lines, data, path):
    """Emit one TOML table: scalar pairs first, then sub-tables,
    then arrays of tables (TOML needs that order)."""
    for key, value in data.items():
        if isinstance(value, dict) or _is_array_of_tables(value):
            continue
        lines.append("%s = %s" % (_toml_key(key), _toml_value(value)))
    for key, value in data.items():
        if isinstance(value, dict):
            lines.append("")
            lines.append(
                "[%s]"
                % ".".join(_toml_key(part) for part in path + [key])
            )
            _emit_toml_table(lines, value, path + [key])
    for key, value in data.items():
        if _is_array_of_tables(value):
            for item in value:
                lines.append("")
                lines.append(
                    "[[%s]]"
                    % ".".join(_toml_key(part) for part in path + [key])
                )
                _emit_toml_table(lines, item, path + [key])


def to_toml(data):
    """Render Python data as TOML text. Top level must be a mapping."""
    lines = []
    _emit_toml_table(lines, data, [])
    return "\n".join(lines).strip("\n") + "\n"


# ---------------------------------------------------------------------------
# YAML writer
# ---------------------------------------------------------------------------

_YAML_DOUBLE_ESCAPES = {
    "\\": "\\\\",
    '"': '\\"',
    "\n": "\\n",
    "\t": "\\t",
    "\r": "\\r",
    "\b": "\\b",
    "\f": "\\f",
}


def _yaml_double_quoted(value):
    out = ['"']
    for ch in value:
        if ch in _YAML_DOUBLE_ESCAPES:
            out.append(_YAML_DOUBLE_ESCAPES[ch])
        elif ord(ch) < 0x20 or ord(ch) == 0x7F:
            out.append("\\x%02X" % ord(ch))
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def _yaml_plain_safe(value):
    """True when a string can be written plain and read back as itself."""
    if value == "" or value != value.strip():
        return False
    if value[0] in "#&*!|>'\"%@`":
        return False
    if value[0] in "-?:" and (len(value) == 1 or value[1] == " "):
        return False
    if value[0] in "[{," or value[0] == "]" or value[0] == "}":
        return False
    if ": " in value or value.endswith(":"):
        return False
    if " #" in value:
        return False
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in value):
        return False
    resolved = resolve_plain(value)
    return isinstance(resolved, str) and resolved == value


def _yaml_scalar(value):
    if value is None:
        return "~"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if math.isnan(value):
            return ".nan"
        if math.isinf(value):
            return ".inf" if value > 0 else "-.inf"
        return repr(value)
    if isinstance(value, str):
        if _yaml_plain_safe(value):
            return value
        return _yaml_double_quoted(value)
    raise ConvertError(
        "refused: value of type %s has no YAML form here"
        % type(value).__name__
    )


def _emit_yaml_mapping(lines, data, indent, first_prefix=None):
    """Emit a mapping. first_prefix replaces the indent on the first
    key line (used for '- key: value' sequence items)."""
    first = True
    for key, value in data.items():
        rendered_key = (
            key if _yaml_plain_safe(key) else _yaml_double_quoted(key)
        )
        if first and first_prefix is not None:
            prefix = first_prefix
            first = False
        else:
            prefix = " " * indent
        if isinstance(value, dict):
            if not value:
                lines.append("%s%s: {}" % (prefix, rendered_key))
            else:
                lines.append("%s%s:" % (prefix, rendered_key))
                _emit_yaml_mapping(lines, value, indent + 2)
        elif isinstance(value, list):
            if not value:
                lines.append("%s%s: []" % (prefix, rendered_key))
            else:
                lines.append("%s%s:" % (prefix, rendered_key))
                _emit_yaml_sequence(lines, value, indent + 2)
        else:
            lines.append(
                "%s%s: %s" % (prefix, rendered_key, _yaml_scalar(value))
            )


def _emit_yaml_sequence(lines, items, indent):
    """Emit a sequence, items indented under their parent key."""
    for item in items:
        if isinstance(item, dict):
            if not item:
                lines.append(" " * indent + "- {}")
            else:
                _emit_yaml_mapping(
                    lines, item, indent + 2,
                    first_prefix=" " * indent + "- ",
                )
        elif isinstance(item, list):
            if not item:
                lines.append(" " * indent + "- []")
            else:
                lines.append(" " * indent + "-")
                _emit_yaml_sequence(lines, item, indent + 2)
        else:
            lines.append(" " * indent + "- " + _yaml_scalar(item))


def to_yaml(data):
    """Render Python data as YAML text. Top level must be a mapping."""
    lines = []
    _emit_yaml_mapping(lines, data, 0)
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Round-trip check
# ---------------------------------------------------------------------------


def values_equal(left, right):
    """Deep equality that treats NaN as equal to NaN, and never lets
    True equal 1: booleans only equal booleans, numbers only numbers."""
    if isinstance(left, bool) or isinstance(right, bool):
        return (
            isinstance(left, bool)
            and isinstance(right, bool)
            and left == right
        )
    if isinstance(left, (int, float)) or isinstance(right, (int, float)):
        if not (
            isinstance(left, (int, float))
            and isinstance(right, (int, float))
        ):
            return False
        if isinstance(left, float) and isinstance(right, float):
            if math.isnan(left) and math.isnan(right):
                return True
        return left == right
    if isinstance(left, str) or isinstance(right, str):
        return isinstance(left, str) and isinstance(right, str) and left == right
    if left is None or right is None:
        return left is None and right is None
    if isinstance(left, list) or isinstance(right, list):
        return (
            isinstance(left, list)
            and isinstance(right, list)
            and len(left) == len(right)
            and all(
                values_equal(a, b) for a, b in zip(left, right)
            )
        )
    if isinstance(left, dict) or isinstance(right, dict):
        return (
            isinstance(left, dict)
            and isinstance(right, dict)
            and set(left) == set(right)
            and all(
                values_equal(left[key], right[key]) for key in left
            )
        )
    return left == right


def convert_text(text, from_format, to_format):
    """Convert text between formats. Returns the converted text.

    Raises ConvertError on bad input, on a refused lossy conversion,
    or when the output does not round-trip back to the input data.
    Nothing is written by this function; callers write only on success.
    """
    if from_format == "yaml":
        data = parse_yaml(text)
    else:
        data = parse_toml(text)
    if to_format == "toml":
        check_toml_compatible(data)
        output = to_toml(data)
        back = parse_toml(output)
    else:
        check_yaml_compatible(data)
        output = to_yaml(data)
        back = parse_yaml(output)
    if not values_equal(data, back):
        raise ConvertError(
            "round-trip check failed: converting to %s and back did "
            "not give the same data" % to_format
        )
    return output


def parse_input(text, input_format):
    """Parse text in the named format (for --check). Returns the data."""
    if input_format == "yaml":
        return parse_yaml(text)
    return parse_toml(text)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser():
    """Build the command-line parser."""
    parser = argparse.ArgumentParser(
        prog="yaml_toml_converter",
        description=(
            "Convert between YAML and TOML, both ways, with a round-trip "
            "check. Refuses lossy conversion instead of guessing. "
            "Exits 1 on bad input or a refusal, 2 on a usage problem."
        ),
    )
    parser.add_argument(
        "files",
        nargs="*",
        metavar="FILE",
        help="File to convert or check. No file reads standard input.",
    )
    parser.add_argument(
        "--from",
        dest="from_format",
        choices=["yaml", "toml"],
        metavar="FORMAT",
        help="Input format (yaml or toml). Needed for stdin or for a "
        "file whose name does not end .yaml, .yml, or .toml.",
    )
    parser.add_argument(
        "--to",
        dest="to_format",
        choices=["yaml", "toml"],
        metavar="FORMAT",
        help="Output format (yaml or toml). Without it, a .yaml file "
        "becomes TOML and a .toml file becomes YAML.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Only validate: print VALID or INVALID per file, do not "
        "convert anything.",
    )
    parser.add_argument(
        "--output",
        metavar="FILE",
        help="Write the converted text to this file instead of "
        "standard output. Only with one input file.",
    )
    parser.add_argument(
        "--no-verify",
        action="store_true",
        help="Skip the round-trip check (not recommended).",
    )
    parser.add_argument(
        "--version",
        action="version",
        version="%(prog)s " + VERSION,
    )
    return parser


def detect_format(path):
    """Guess the format from a file name, or None when unknown."""
    suffix = Path(path).suffix.lower()
    if suffix in (".yaml", ".yml"):
        return "yaml"
    if suffix == ".toml":
        return "toml"
    return None


def read_input(path):
    """Read a file as UTF-8 text. Raises OSError / UnicodeDecodeError."""
    return Path(path).read_text(encoding="utf-8")


def describe_error(source, error):
    """Build the one-line report for a conversion failure."""
    if isinstance(error, YamlError) and error.lineno is not None:
        return "INVALID %s:%d: %s" % (source, error.lineno, error.message)
    if error.message.startswith("refused:"):
        return "REFUSED %s: %s" % (source, error.message[len("refused:"):].strip())
    return "INVALID %s: %s" % (source, error.message)


def process_check(source, text, input_format, out_lines):
    """Validate one input. Returns True when it parses in the subset."""
    try:
        parse_input(text, input_format)
    except ConvertError as error:
        out_lines.append(describe_error(source, error))
        return False
    out_lines.append("VALID   %s" % source)
    return True


def run(argv=None, stdin_text=None, stdout=None, stderr=None):
    """Run the CLI. Returns the exit code. Testable without subprocess."""
    stdout = sys.stdout if stdout is None else stdout
    stderr = sys.stderr if stderr is None else stderr
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.output and args.check:
        print("error: --output cannot be used with --check", file=stderr)
        return 2
    if args.to_format and args.check:
        print("error: --to cannot be used with --check", file=stderr)
        return 2
    if args.output and len(args.files) != 1:
        print("error: --output needs exactly one input file", file=stderr)
        return 2
    if not args.check and len(args.files) > 1:
        print(
            "error: converting more than one file needs --check",
            file=stderr,
        )
        return 2
    if args.no_verify and args.check:
        print("error: --no-verify cannot be used with --check", file=stderr)
        return 2

    inputs = []  # list of (source_name, text, input_format)
    if not args.files:
        if stdin_text is None:
            stdin_text = sys.stdin.read()
        if args.from_format is None:
            print(
                "error: stdin needs --from yaml or --from toml",
                file=stderr,
            )
            return 2
        inputs.append(("<stdin>", stdin_text, args.from_format))
    else:
        for path in args.files:
            input_format = args.from_format or detect_format(path)
            if input_format is None:
                print(
                    "error: cannot tell the format of %s, use --from "
                    "yaml or --from toml" % path,
                    file=stderr,
                )
                return 2
            try:
                inputs.append((path, read_input(path), input_format))
            except FileNotFoundError:
                print("error: file not found: %s" % path, file=stderr)
                return 2
            except OSError as exc:
                print("error: cannot read %s: %s" % (path, exc), file=stderr)
                return 2
            except UnicodeDecodeError:
                print("error: %s is not valid UTF-8 text" % path, file=stderr)
                return 2

    if args.check:
        all_valid = True
        for source, text, input_format in inputs:
            out_lines = []
            if not process_check(source, text, input_format, out_lines):
                all_valid = False
            for line in out_lines:
                print(line, file=stdout)
        return 0 if all_valid else 1

    source, text, input_format = inputs[0]
    to_format = args.to_format
    if to_format is None:
        to_format = "toml" if input_format == "yaml" else "yaml"
    if to_format == input_format:
        print(
            "error: --to %s with a %s input converts nothing; "
            "pick the other format" % (to_format, input_format),
            file=stderr,
        )
        return 2
    try:
        if args.no_verify:
            if input_format == "yaml":
                data = parse_yaml(text)
            else:
                data = parse_toml(text)
            if to_format == "toml":
                check_toml_compatible(data)
                output = to_toml(data)
            else:
                check_yaml_compatible(data)
                output = to_yaml(data)
        else:
            output = convert_text(text, input_format, to_format)
    except ConvertError as error:
        print(describe_error(source, error), file=stdout)
        return 1
    if args.output:
        try:
            Path(args.output).write_text(output, encoding="utf-8")
        except OSError as exc:
            print(
                "error: cannot write %s: %s" % (args.output, exc),
                file=stderr,
            )
            return 2
        print("converted %s -> %s" % (source, args.output), file=stdout)
    else:
        print(output, file=stdout, end="")
    return 0


def main():
    sys.exit(run())


if __name__ == "__main__":
    main()

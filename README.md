# yaml-toml-converter-cli

Convert YAML to TOML and back, with a round-trip check, refuse lossy conversion.

[![CI](https://github.com/haiderstudio/yaml-toml-converter-cli/actions/workflows/ci.yml/badge.svg)](https://github.com/haiderstudio/yaml-toml-converter-cli/actions/workflows/ci.yml) [![Licence: MIT](https://img.shields.io/badge/Licence-MIT-yellow.svg)](LICENSE)

## What it does

`yaml_toml_converter` converts one file between YAML and TOML, both
ways. It reads TOML with Python's standard `tomllib` module and writes
TOML itself, and it reads and writes a documented subset of YAML with
its own small standard-library parser (see "Supported YAML subset"
below) — no third-party packages, no install beyond Python.

- A `.yaml` or `.yml` file becomes TOML; a `.toml` file becomes YAML.
  `--to` picks the direction, `--from` names the input format for
  standard input or for a file whose name gives no hint.
- Every conversion is checked: the tool re-reads its own output and
  compares it with the input data. A conversion that does not
  round-trip is reported and nothing is written.
- It refuses lossy conversion instead of guessing: a YAML null value
  has no TOML form, a TOML date has no YAML form here, a non-mapping
  top level has no TOML form, and YAML anchors, aliases, tags, and
  merge keys are outside the supported subset. A refusal names the
  exact value and writes nothing.
- `--check` only validates: it prints `VALID` or `INVALID` per file
  and exits 1 when any file fails, so it can fail a CI run.
- It never rewrites a file unless you ask: `--output FILE` writes
  the converted text to the file you name. With no output flag the
  converted text goes to standard output and the input file is left
  alone.
- It is report-only and runs fully offline: it sends nothing anywhere
  and needs no API key.

## What it does NOT do

- It does not convert JSON, INI, XML, or any other format. YAML and
  TOML only, both ways.
- It does not read full YAML. It reads the documented subset below
  and refuses the rest (anchors, aliases, tags, merge keys, complex
  keys, multi-document files, tabs for indentation). Full YAML needs
  a YAML library; this tool is for plain config files.
- It does not preserve comments. Comments are read and dropped; the
  converted file has the same data, not the same comments.
- It does not convert dates. A TOML date or datetime has no plain
  YAML form here, so converting a file that has one is refused
  rather than turned into a string that looks like a date but is not.
- It does not convert a top level that is not a mapping to TOML.
  TOML has no top-level list or scalar, so a YAML file whose top
  level is a list or a plain value is refused for TOML output.
- It does not fix bad input. A file that does not parse is reported
  with its line number, not repaired.

## Who it is for

Anyone who keeps config in YAML in one place and TOML in another and
needs to move a file between them without installing anything: a
developer converting a settings file for a tool that reads TOML, or
checking a batch of config files in CI before a deploy. If the file
converts cleanly, you get the same data in the other format; if it
cannot convert without losing something, the report says exactly
what would be lost.

## Supported YAML subset

This is the whole list. Anything not listed here is refused, with
the reason in the report.

| Feature | Supported? |
| --- | --- |
| Block mappings, nested, any consistent indent | Yes |
| Block sequences, including sequences of mappings | Yes |
| Flow mappings `{a: 1}` and flow sequences `[a, b]`, on one line | Yes |
| Plain scalars: strings, ints (decimal, `0x`, `0o`), floats, `true`/`false`, `null` / `~` / empty | Yes |
| Single-quoted and double-quoted strings | Yes |
| Comments (full-line and trailing) | Yes, read and dropped |
| Literal `\|` and folded `>` block scalars, with `-` / `+` chomping | Yes |
| `---` document marker, `...` end marker | Yes |
| `yes` / `no` / `on` / `off` as booleans | No — plain strings (YAML 1.2 core schema) |
| Anchors (`&x`), aliases (`*x`), tags (`!!str`), merge keys (`<<`) | No — refused |
| Complex keys (`? key`), directives (`%YAML`), multi-document files | No — refused |
| Tabs for indentation | No — refused |
| Multi-line flow collections | No — refused (must fit on one line) |
| Duplicate keys in one mapping | No — refused |
| Dates and times as values | No — YAML timestamps are read as plain strings |

TOML is read in full by the standard library (`tomllib`), so any
valid TOML parses. The limits on the TOML side are about output:
null has no TOML form, and dates have no YAML form here, so those
two cases are refused at conversion time, not at read time.

## How this differs from similar tools

YAML↔TOML conversion already exists, and the broad tools do more:
sclevine/yj (1.1k stars) converts between YAML, TOML, JSON, and HCL
and needs a Go install; remarshal-project/remarshal (552 stars)
converts between CBOR, JSON, MessagePack, TOML, and YAML and needs
a PyPI install, and it turns dates and nulls into strings instead
of refusing. The multi-format converters on PyPI (configconverter,
universal-config-converter) cover JSON, YAML, TOML, INI, XML, and
`.env` and need Poetry or click. mikefarah/yq is a full query
language for YAML and TOML, not a converter. The exact-name search
for `yaml-toml-converter` on GitHub finds 3 results, all 0 stars
(one archived Python CLI, one JavaScript tool, one TypeScript tool).

This tool does a narrower job: a Python standard-library-only CLI
that converts YAML and TOML only, both ways, checks every
conversion by round-tripping it, and refuses lossy conversion
instead of guessing — no install beyond Python, no other formats,
no query language, no silent type changes.

## Install

Python standard library only — no packages to download. Clone the
repo and run it in place:

```sh
git clone https://github.com/haiderstudio/yaml-toml-converter-cli.git
cd yaml-toml-converter-cli
```

Python 3.11 or newer (the standard `tomllib` module). CI runs
Python 3.12.

## Usage

Convert a YAML file to TOML:

```sh
python3 src/yaml_toml_converter.py examples/config.yaml
```

Real output:

```text
name = "example-site"
version = "1.4.0"
draft = false
private = false
tags = ["fast", "small"]

[settings]
postsPerPage = 10
theme = "light"

[[pages]]
path = "/"
title = "Home"
draft = false

[[pages]]
path = "/about"
title = "About"
draft = false
```

The command exits 0. The input file is left alone.

Convert a TOML file back to YAML:

```sh
python3 src/yaml_toml_converter.py examples/config.toml
```

Real output:

```text
name: example-site
version: 1.4.0
draft: false
private: false
tags:
  - fast
  - small
settings:
  postsPerPage: 10
  theme: light
pages:
  - path: /
    title: Home
    draft: false
  - path: /about
    title: About
    draft: false
```

The two files hold the same data: converting either one gives the
other file back, byte for byte.

Check several files at once:

```sh
python3 src/yaml_toml_converter.py --check examples/config.yaml examples/config.toml examples/nested.yaml examples/nested.toml examples/lossy-dates.toml examples/lossy-null.yaml
```

Real output:

```text
VALID   examples/config.yaml
VALID   examples/config.toml
VALID   examples/nested.yaml
VALID   examples/nested.toml
VALID   examples/lossy-dates.toml
VALID   examples/lossy-null.yaml
```

The command exits 0. Note that `lossy-dates.toml` and
`lossy-null.yaml` are VALID here: they parse fine. They only fail
when converted, because the conversion itself would lose data.

Convert from a pipe:

```sh
echo 'a: 1' | python3 src/yaml_toml_converter.py --from yaml --to toml
```

Real output:

```text
a = 1
```

Write the converted text to a file:

```sh
python3 src/yaml_toml_converter.py --output config.toml examples/config.yaml
```

Real output:

```text
converted examples/config.yaml -> config.toml
```

## Examples

### 1. A file with dates is refused, not guessed

`examples/lossy-dates.toml` has a date and a datetime:

```sh
python3 src/yaml_toml_converter.py --to yaml examples/lossy-dates.toml
```

```text
REFUSED examples/lossy-dates.toml: date/time value at released has no YAML form here
```

The command exits 1 and writes nothing. Turning the date into the
string `"2026-11-06"` would look right and be wrong — a string is
not a date — so the tool stops instead.

### 2. A null value is refused for TOML

`examples/lossy-null.yaml` has `notes: ~`:

```sh
python3 src/yaml_toml_converter.py --to toml examples/lossy-null.yaml
```

```text
REFUSED examples/lossy-null.yaml: null value at notes has no TOML form
```

The command exits 1 and writes nothing. TOML has no null, so there
is no honest way to write that value.

### 3. Fail CI on a file that does not parse

```sh
python3 src/yaml_toml_converter.py --check config.yaml
echo $?
```

```text
0
```

The exit code is 0 when the file is valid. With a bad file the
same command prints `INVALID config.yaml:2: ...` and exits 1. Put
the same command in CI and the build fails until the file is fixed.

## How it is tested

```sh
python3 tests/test_yaml_toml_converter.py
```

84 tests, standard library `unittest` only — no third-party
packages. They cover: plain-scalar resolution (nulls, booleans,
ints, floats, `yes`/`no` staying strings), the YAML parser
(nested mappings, sequences of mappings, nested sequences, flow
collections, quoted strings and escapes, comments, document
markers, literal and folded block scalars), the refusals
(anchors, aliases, tags, merge keys, multi-document files, tabs,
bad indentation, duplicate keys, unclosed quotes and flow
collections), both writers (key quoting, string escaping, nested
tables, arrays of tables, empty collections, quoting strings that
look like other types), the round-trip check (real conversions both
ways, `True` never equalling `1`, NaN handling), the CLI (file and
stdin input, `--to` / `--from`, `--output`, `--check`, usage
errors, a failed conversion writing nothing), the bundled examples
(both pairs round-trip byte for byte, the committed
`examples/sample-output.txt` compared against a fresh run of the
example script, the README's quoted outputs checked against that
file), the fresh-folder install, and a check that the code and the
tests import standard library only.

The bundled example is a real run, not made-up output:

```sh
sh examples/run-example.sh
```

It converts `examples/config.yaml` to TOML and back, checks all
six example files, and shows both refusals. The committed
`examples/sample-output.txt` is the output of that run.

## Contributing

Small, focused improvements are welcome — see
[CONTRIBUTING.md](CONTRIBUTING.md).

## Licence

MIT — see [LICENSE](LICENSE).

## Original work

Built from scratch for this repo. TOML parsing comes from Python's
standard `tomllib` module; the YAML subset parser, the TOML writer,
and the YAML writer are original code written for this tool. No
code was copied from any existing converter.

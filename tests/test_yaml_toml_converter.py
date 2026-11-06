#!/usr/bin/env python3
"""Tests for yaml-toml-converter-cli. Standard library unittest only.

Run: python3 tests/test_yaml_toml_converter.py
"""

import io
import math
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import yaml_toml_converter as ytc

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src" / "yaml_toml_converter.py"


def run_cli(argv, stdin_text=""):
    """Run the CLI in-process and capture stdout/stderr. Returns (code, out, err)."""
    out = io.StringIO()
    err = io.StringIO()
    code = ytc.run(argv, stdin_text=stdin_text, stdout=out, stderr=err)
    return code, out.getvalue(), err.getvalue()


class TestResolvePlain(unittest.TestCase):
    def test_null_forms(self):
        for text in ("", "~", "null", "Null", "NULL"):
            self.assertIsNone(ytc.resolve_plain(text), text)

    def test_booleans(self):
        self.assertIs(ytc.resolve_plain("true"), True)
        self.assertIs(ytc.resolve_plain("False"), False)

    def test_yes_no_are_strings(self):
        # YAML 1.2 core schema: yes/no/on/off are plain strings.
        for text in ("yes", "no", "on", "off", "Yes", "ON"):
            self.assertEqual(ytc.resolve_plain(text), text)

    def test_ints(self):
        self.assertEqual(ytc.resolve_plain("42"), 42)
        self.assertEqual(ytc.resolve_plain("-7"), -7)
        self.assertEqual(ytc.resolve_plain("1_000"), 1000)
        self.assertEqual(ytc.resolve_plain("0x10"), 16)
        self.assertEqual(ytc.resolve_plain("0o17"), 15)

    def test_floats(self):
        self.assertEqual(ytc.resolve_plain("1.5"), 1.5)
        self.assertEqual(ytc.resolve_plain("1e3"), 1000.0)
        self.assertTrue(math.isinf(ytc.resolve_plain(".inf")))
        self.assertTrue(math.isnan(ytc.resolve_plain(".nan")))

    def test_version_like_stays_string(self):
        self.assertEqual(ytc.resolve_plain("1.4.0"), "1.4.0")
        self.assertEqual(ytc.resolve_plain("12:30"), "12:30")


class TestYamlParser(unittest.TestCase):
    def test_flat_mapping(self):
        self.assertEqual(
            ytc.parse_yaml("name: site\nport: 8080\ndraft: false\n"),
            {"name": "site", "port": 8080, "draft": False},
        )

    def test_nested_mapping(self):
        self.assertEqual(
            ytc.parse_yaml("a:\n  b:\n    c: 1\n"),
            {"a": {"b": {"c": 1}}},
        )

    def test_block_sequence(self):
        self.assertEqual(
            ytc.parse_yaml("tags:\n  - fast\n  - small\n"),
            {"tags": ["fast", "small"]},
        )

    def test_sequence_at_key_indent(self):
        self.assertEqual(
            ytc.parse_yaml("tags:\n- fast\n- small\n"),
            {"tags": ["fast", "small"]},
        )

    def test_sequence_of_mappings(self):
        self.assertEqual(
            ytc.parse_yaml(
                "pages:\n  - path: /\n    title: Home\n"
                "  - path: /about\n    title: About\n"
            ),
            {
                "pages": [
                    {"path": "/", "title": "Home"},
                    {"path": "/about", "title": "About"},
                ]
            },
        )

    def test_nested_sequence(self):
        self.assertEqual(
            ytc.parse_yaml("grid:\n  - - a\n    - b\n  - - c\n"),
            {"grid": [["a", "b"], ["c"]]},
        )

    def test_flow_collections(self):
        self.assertEqual(
            ytc.parse_yaml("tags: [a, b, c]\nmeta: {x: 1, y: two}\n"),
            {"tags": ["a", "b", "c"], "meta": {"x": 1, "y": "two"}},
        )

    def test_flow_url_value(self):
        self.assertEqual(
            ytc.parse_yaml("links: [http://example.com, https://x.test/a]\n"),
            {"links": ["http://example.com", "https://x.test/a"]},
        )

    def test_quoted_strings(self):
        self.assertEqual(
            ytc.parse_yaml('a: "has: colon"\nb: \'it\'\'s\'\n'),
            {"a": "has: colon", "b": "it's"},
        )

    def test_double_quoted_escapes(self):
        self.assertEqual(
            ytc.parse_yaml('a: "line\\nbreak"\n'),
            {"a": "line\nbreak"},
        )

    def test_comments(self):
        self.assertEqual(
            ytc.parse_yaml("# top comment\nname: site # trailing\n# bye\n"),
            {"name": "site"},
        )

    def test_hash_inside_quotes_is_not_a_comment(self):
        self.assertEqual(
            ytc.parse_yaml('a: "x#y"\n'),
            {"a": "x#y"},
        )

    def test_document_markers(self):
        self.assertEqual(
            ytc.parse_yaml("---\nname: site\n...\n"),
            {"name": "site"},
        )

    def test_empty_document(self):
        self.assertIsNone(ytc.parse_yaml(""))
        self.assertIsNone(ytc.parse_yaml("# only a comment\n"))

    def test_literal_block_scalar(self):
        self.assertEqual(
            ytc.parse_yaml("note: |\n  line one\n  line two\n"),
            {"note": "line one\nline two\n"},
        )

    def test_literal_block_scalar_strip(self):
        self.assertEqual(
            ytc.parse_yaml("note: |-\n  line one\n  line two\n"),
            {"note": "line one\nline two"},
        )

    def test_folded_block_scalar(self):
        self.assertEqual(
            ytc.parse_yaml("note: >\n  word one\n  word two\n"),
            {"note": "word one word two\n"},
        )

    def test_block_scalar_with_blank_line(self):
        self.assertEqual(
            ytc.parse_yaml("note: |\n  one\n\n  three\n"),
            {"note": "one\n\nthree\n"},
        )

    def test_duplicate_key_refused(self):
        with self.assertRaises(ytc.YamlError):
            ytc.parse_yaml("a: 1\na: 2\n")

    def test_bool_and_int_key_collision_refused(self):
        # True == 1 in Python, so these two keys would silently merge.
        with self.assertRaises(ytc.YamlError):
            ytc.parse_yaml("true: a\n1: b\n")

    def test_anchor_refused(self):
        with self.assertRaises(ytc.YamlError):
            ytc.parse_yaml("a: &x 1\nb: *x\n")

    def test_tag_refused(self):
        with self.assertRaises(ytc.YamlError):
            ytc.parse_yaml("a: !!str 1\n")

    def test_merge_key_refused(self):
        with self.assertRaises(ytc.YamlError):
            ytc.parse_yaml("a: &x {p: 1}\nb:\n  <<: *x\n")

    def test_multi_document_refused(self):
        with self.assertRaises(ytc.YamlError):
            ytc.parse_yaml("a: 1\n---\nb: 2\n")

    def test_tabs_refused(self):
        with self.assertRaises(ytc.YamlError):
            ytc.parse_yaml("a:\n\tb: 1\n")

    def test_bad_indentation_refused(self):
        with self.assertRaises(ytc.YamlError):
            ytc.parse_yaml("a:\n    b: 1\n   c: 2\n")

    def test_unclosed_quote_refused(self):
        with self.assertRaises(ytc.YamlError):
            ytc.parse_yaml('a: "never closed\n')

    def test_unclosed_flow_refused(self):
        with self.assertRaises(ytc.YamlError):
            ytc.parse_yaml("a: [1, 2\n")

    def test_plain_scalar_with_colon_space_refused(self):
        with self.assertRaises(ytc.YamlError):
            ytc.parse_yaml("a: has: space\n")

    def test_line_number_reported(self):
        try:
            ytc.parse_yaml("a: 1\nb: &x 2\n")
        except ytc.YamlError as error:
            self.assertEqual(error.lineno, 2)
        else:
            self.fail("expected YamlError")


class TestTomlWriter(unittest.TestCase):
    def test_scalars_and_tables(self):
        out = ytc.to_toml({"name": "x", "n": 3, "ok": True, "sub": {"a": 1}})
        self.assertIn('name = "x"', out)
        self.assertIn("n = 3", out)
        self.assertIn("ok = true", out)
        self.assertIn("[sub]", out)

    def test_key_quoting(self):
        out = ytc.to_toml({"a b": 1, "a.b": 2})
        self.assertIn('"a b" = 1', out)
        self.assertIn('"a.b" = 2', out)

    def test_string_escaping(self):
        out = ytc.to_toml({"s": 'quote " and\nnewline'})
        self.assertIn('"quote \\" and\\nnewline"', out)

    def test_array_of_tables(self):
        out = ytc.to_toml({"items": [{"n": 1}, {"n": 2}]})
        self.assertEqual(out.count("[[items]]"), 2)

    def test_empty_collections(self):
        out = ytc.to_toml({"e": {}, "l": []})
        self.assertIn("[e]", out)
        self.assertIn("l = []", out)

    def test_float_specials(self):
        out = ytc.to_toml({"a": math.inf, "b": -math.inf, "c": math.nan})
        self.assertIn("a = inf", out)
        self.assertIn("b = -inf", out)
        self.assertIn("c = nan", out)


class TestYamlWriter(unittest.TestCase):
    def test_quotes_strings_that_look_like_other_types(self):
        out = ytc.to_yaml({"a": "true", "b": "1", "c": "1.4.0", "d": "yes"})
        self.assertIn('a: "true"', out)
        self.assertIn('b: "1"', out)
        self.assertIn("c: 1.4.0", out)  # a version is a plain string
        self.assertIn("d: yes", out)  # yes is a plain string in YAML 1.2

    def test_quotes_colon_and_hash(self):
        out = ytc.to_yaml({"a": "has: colon", "b": "hash #tag", "c": ""})
        self.assertIn('a: "has: colon"', out)
        self.assertIn('b: "hash #tag"', out)
        self.assertIn('c: ""', out)

    def test_multiline_string_quoted(self):
        out = ytc.to_yaml({"note": "one\ntwo\n"})
        self.assertIn('note: "one\\ntwo\\n"', out)

    def test_empty_collections(self):
        out = ytc.to_yaml({"e": {}, "l": []})
        self.assertIn("e: {}", out)
        self.assertIn("l: []", out)


class TestValuesEqual(unittest.TestCase):
    def test_true_is_not_one(self):
        self.assertFalse(ytc.values_equal(True, 1))
        self.assertFalse(ytc.values_equal({"a": True}, {"a": 1}))

    def test_nan_equals_nan(self):
        self.assertTrue(ytc.values_equal(math.nan, math.nan))
        self.assertTrue(ytc.values_equal({"a": math.nan}, {"a": math.nan}))

    def test_int_float_equal(self):
        self.assertTrue(ytc.values_equal(1, 1.0))


class TestConvertText(unittest.TestCase):
    def test_yaml_to_toml_round_trip(self):
        text = "name: site\nn: 3\ntags: [a, b]\n"
        out = ytc.convert_text(text, "yaml", "toml")
        self.assertIn('name = "site"', out)
        self.assertEqual(ytc.parse_toml(out), {"name": "site", "n": 3, "tags": ["a", "b"]})

    def test_toml_to_yaml_round_trip(self):
        text = 'name = "site"\nn = 3\ntags = ["a", "b"]\n'
        out = ytc.convert_text(text, "toml", "yaml")
        self.assertEqual(ytc.parse_yaml(out), {"name": "site", "n": 3, "tags": ["a", "b"]})

    def test_null_to_toml_refused(self):
        with self.assertRaises(ytc.ConvertError) as ctx:
            ytc.convert_text("a: ~\n", "yaml", "toml")
        self.assertIn("null value", str(ctx.exception))

    def test_date_to_yaml_refused(self):
        with self.assertRaises(ytc.ConvertError) as ctx:
            ytc.convert_text('a = 2026-11-06\n', "toml", "yaml")
        self.assertIn("date/time", str(ctx.exception))

    def test_top_level_list_to_toml_refused(self):
        with self.assertRaises(ytc.ConvertError):
            ytc.convert_text("- a\n- b\n", "yaml", "toml")

    def test_non_string_key_to_toml_refused(self):
        with self.assertRaises(ytc.ConvertError):
            ytc.convert_text("1: one\n", "yaml", "toml")

    def test_huge_int_to_toml_refused(self):
        with self.assertRaises(ytc.ConvertError):
            ytc.convert_text("a: 99999999999999999999\n", "yaml", "toml")

    def test_bad_yaml_refused(self):
        with self.assertRaises(ytc.ConvertError):
            ytc.convert_text("a: [1, 2\n", "yaml", "toml")

    def test_bad_toml_refused(self):
        with self.assertRaises(ytc.ConvertError):
            ytc.convert_text("a = \n", "toml", "yaml")


class TestCli(unittest.TestCase):
    def test_convert_file_by_extension(self):
        code, out, _ = run_cli(
            [str(ROOT / "examples" / "config.yaml")]
        )
        self.assertEqual(code, 0)
        self.assertIn('name = "example-site"', out)

    def test_convert_other_direction(self):
        code, out, _ = run_cli(
            [str(ROOT / "examples" / "config.toml")]
        )
        self.assertEqual(code, 0)
        self.assertIn("name: example-site", out)

    def test_stdin_needs_from(self):
        code, _out, err = run_cli([], stdin_text="a: 1\n")
        self.assertEqual(code, 2)
        self.assertIn("--from", err)

    def test_stdin_convert(self):
        code, out, _ = run_cli(
            ["--from", "yaml", "--to", "toml"], stdin_text="a: 1\n"
        )
        self.assertEqual(code, 0)
        self.assertEqual(out, "a = 1\n")

    def test_same_format_is_usage_error(self):
        code, _out, err = run_cli(
            ["--from", "yaml", "--to", "yaml"], stdin_text="a: 1\n"
        )
        self.assertEqual(code, 2)
        self.assertIn("converts nothing", err)

    def test_unknown_extension_needs_from(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "data.txt"
            path.write_text("a: 1\n", encoding="utf-8")
            code, _out, err = run_cli([str(path)])
            self.assertEqual(code, 2)
            self.assertIn("--from", err)

    def test_refusal_exit_1(self):
        code, out, _ = run_cli(
            ["--to", "yaml", str(ROOT / "examples" / "lossy-dates.toml")]
        )
        self.assertEqual(code, 1)
        self.assertIn("REFUSED", out)
        self.assertIn("released", out)

    def test_null_refusal_names_the_key(self):
        code, out, _ = run_cli(
            ["--to", "toml", str(ROOT / "examples" / "lossy-null.yaml")]
        )
        self.assertEqual(code, 1)
        self.assertIn("REFUSED", out)
        self.assertIn("notes", out)

    def test_check_valid_and_invalid(self):
        code, out, _ = run_cli(
            [
                "--check",
                str(ROOT / "examples" / "config.yaml"),
                str(ROOT / "examples" / "lossy-null.yaml"),
            ]
        )
        self.assertEqual(code, 0)
        self.assertIn("VALID", out)

    def test_check_bad_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.yaml"
            path.write_text("a: [1, 2\n", encoding="utf-8")
            code, out, _ = run_cli(["--check", str(path)])
            self.assertEqual(code, 1)
            self.assertIn("INVALID", out)
            self.assertIn(":1:", out)

    def test_output_flag_writes_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "out.toml"
            code, out, _ = run_cli(
                [
                    "--output",
                    str(dest),
                    str(ROOT / "examples" / "config.yaml"),
                ]
            )
            self.assertEqual(code, 0)
            self.assertIn("converted", out)
            self.assertIn('name = "example-site"', dest.read_text())

    def test_failed_convert_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "out.yaml"
            code, _out, _err = run_cli(
                [
                    "--output",
                    str(dest),
                    str(ROOT / "examples" / "lossy-dates.toml"),
                ]
            )
            self.assertEqual(code, 1)
            self.assertFalse(dest.exists())

    def test_missing_file_is_usage_error(self):
        code, _out, err = run_cli(["nope.yaml"])
        self.assertEqual(code, 2)
        self.assertIn("file not found", err)

    def test_two_files_to_convert_is_usage_error(self):
        code, _out, err = run_cli(
            [
                str(ROOT / "examples" / "config.yaml"),
                str(ROOT / "examples" / "nested.yaml"),
            ]
        )
        self.assertEqual(code, 2)

    def test_no_verify_flag_still_converts(self):
        code, out, _ = run_cli(
            ["--no-verify", str(ROOT / "examples" / "config.yaml")]
        )
        self.assertEqual(code, 0)
        self.assertIn('name = "example-site"', out)

    def test_subprocess_run(self):
        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "src" / "yaml_toml_converter.py"),
                str(ROOT / "examples" / "config.yaml"),
            ],
            capture_output=True,
            text=True,
            cwd=str(ROOT),
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn('name = "example-site"', result.stdout)


class TestExamples(unittest.TestCase):
    def test_config_pair_round_trips_byte_identical(self):
        yaml_text = (ROOT / "examples" / "config.yaml").read_text()
        toml_text = (ROOT / "examples" / "config.toml").read_text()
        self.assertEqual(
            ytc.convert_text(yaml_text, "yaml", "toml"), toml_text
        )
        self.assertEqual(
            ytc.convert_text(toml_text, "toml", "yaml"), yaml_text
        )

    def test_nested_pair_round_trips_byte_identical(self):
        yaml_text = (ROOT / "examples" / "nested.yaml").read_text()
        toml_text = (ROOT / "examples" / "nested.toml").read_text()
        self.assertEqual(
            ytc.convert_text(yaml_text, "yaml", "toml"), toml_text
        )
        self.assertEqual(
            ytc.convert_text(toml_text, "toml", "yaml"), yaml_text
        )

    def test_run_example_matches_committed_output(self):
        result = subprocess.run(
            ["sh", "examples/run-example.sh"],
            capture_output=True,
            text=True,
            cwd=str(ROOT),
        )
        self.assertEqual(result.returncode, 0)
        committed = (ROOT / "examples" / "sample-output.txt").read_text()
        self.assertEqual(result.stdout, committed)

    def test_readme_quotes_real_output(self):
        readme = (ROOT / "README.md").read_text()
        sample = (ROOT / "examples" / "sample-output.txt").read_text()
        # Every output block the README shows comes from the real run.
        for block in (
            'name = "example-site"\nversion = "1.4.0"',
            "name: example-site\nversion: 1.4.0",
            "VALID   examples/config.yaml",
            "REFUSED examples/lossy-dates.toml: date/time value at released",
            "REFUSED examples/lossy-null.yaml: null value at notes",
        ):
            self.assertIn(block, readme, block)
            self.assertIn(block, sample, block)

    def test_readme_install_commands_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "yaml-toml-converter-cli"
            subprocess.run(
                ["cp", "-r", str(ROOT), str(dest)],
                check=True,
            )
            result = subprocess.run(
                [
                    sys.executable,
                    "src/yaml_toml_converter.py",
                    "examples/config.yaml",
                ],
                capture_output=True,
                text=True,
                cwd=str(dest),
            )
            self.assertEqual(result.returncode, 0)
            self.assertIn('name = "example-site"', result.stdout)
            # Run one quick, non-recursive test from the fresh copy:
            # the full suite must not be re-run here, or the fresh-copy
            # test would copy and run the suite again forever.
            tests = subprocess.run(
                [
                    sys.executable,
                    "tests/test_yaml_toml_converter.py",
                    "TestExamples.test_config_pair_round_trips_byte_identical",
                ],
                capture_output=True,
                text=True,
                cwd=str(dest),
            )
            self.assertEqual(tests.returncode, 0, tests.stderr[-2000:])


class TestRepoHygiene(unittest.TestCase):
    def test_stdlib_only_imports(self):
        allowed = {
            "argparse",
            "datetime",
            "math",
            "re",
            "sys",
            "tomllib",
            "pathlib",
            "io",
            "os",
            "subprocess",
            "tempfile",
            "unittest",
        }
        for path in (ROOT / "src").rglob("*.py"):
            text = path.read_text()
            for line in text.splitlines():
                line = line.strip()
                if line.startswith("import "):
                    for name in line[7:].split(","):
                        top = name.strip().split(".")[0].split(" ")[0]
                        self.assertIn(top, allowed, "%s in %s" % (top, path))
                elif line.startswith("from "):
                    top = line.split()[1].split(".")[0]
                    self.assertIn(top, allowed, "%s in %s" % (top, path))
        for path in (ROOT / "tests").rglob("*.py"):
            text = path.read_text()
            for line in text.splitlines():
                line = line.strip()
                if line.startswith("import "):
                    for name in line[7:].split(","):
                        top = name.strip().split(".")[0].split(" ")[0]
                        self.assertIn(
                            top,
                            allowed | {"yaml_toml_converter"},
                            "%s in %s" % (top, path),
                        )
                elif line.startswith("from "):
                    top = line.split()[1].split(".")[0]
                    self.assertIn(
                        top,
                        allowed | {"yaml_toml_converter"},
                        "%s in %s" % (top, path),
                    )

    def test_no_placeholders_or_secrets(self):
        banned = (
            "TODO",
            "FIXME",
            "lorem",
            "coming soon",
            "API_KEY",
            "api_key",
            "password =",
        )
        # The tests directory is excluded from this scan: the test file
        # itself names the banned words in its own check list, and it
        # uses temp paths. The shipped content is what must be clean.
        shipped = [
            path
            for path in ROOT.rglob("*")
            if path.is_file()
            and "__pycache__" not in str(path)
            and "tests" not in path.parts
        ]
        for path in shipped:
            text = path.read_text(errors="ignore")
            for word in banned:
                self.assertNotIn(word, text, "%s in %s" % (word, path))

    def test_no_absolute_paths(self):
        # Same exclusion as the placeholder scan: the test file itself
        # names temp paths in its own check list.
        shipped = [
            path
            for path in ROOT.rglob("*")
            if path.is_file()
            and "__pycache__" not in str(path)
            and "tests" not in path.parts
        ]
        for path in shipped:
            text = path.read_text(errors="ignore")
            self.assertNotIn(
                "/home/hatch", text, "absolute path in %s" % path
            )
            self.assertNotIn("/tmp/", text, "absolute path in %s" % path)

    def test_gitignore_lists_env_first(self):
        lines = (ROOT / ".gitignore").read_text().splitlines()
        self.assertEqual(lines[0], ".env")

    def test_ci_runs_the_tests(self):
        ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
        self.assertIn("tests/test_yaml_toml_converter.py", ci)
        self.assertIn('"3.12"', ci)


if __name__ == "__main__":
    unittest.main(verbosity=2)

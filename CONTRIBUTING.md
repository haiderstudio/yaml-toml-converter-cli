# Contributing

Small, focused improvements are welcome.

- Open an issue first for anything larger than a typo or a clearer example, so the scope can be agreed before work starts.
- Never weaken the safety rules: a lossy conversion must stay a refusal, and a round-trip check must never be skipped by default.
- Every claim in a proposed change must be verifiable: a command must be runnable, a conversion must round-trip, a refusal must name the exact value.
- Run `python3 tests/test_yaml_toml_converter.py` before opening a pull request. It must pass.
- Do not add placeholder text, invented features, or copied content without attribution.
- Python standard library only — no new dependency without a reason in the issue.

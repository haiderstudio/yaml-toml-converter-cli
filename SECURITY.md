# Security Policy

This tool runs locally, collects no data, sends nothing anywhere, and needs no API key or secret. It only reads the YAML or TOML file or standard input you name, and it never changes a file unless you ask with `--output`. It writes nothing except the converted text or the report on standard output (and usage errors on standard error).

If you find a case where the tool changes a file without `--output`, converts a value without the round-trip check catching a loss, or reports a line number that does not match the real error position, please open an issue describing the exact commands and input. Do not include any real secret in the report.

#!/bin/sh
# Run the converter on the bundled examples.
# Run from the repo root: sh examples/run-example.sh
# The refusal runs exit 1 on purpose — that is the expected result here.
set -e
cd "$(dirname "$0")/.."
echo "--- yaml to toml ---"
python3 src/yaml_toml_converter.py examples/config.yaml
echo "--- toml to yaml ---"
python3 src/yaml_toml_converter.py examples/config.toml
echo "--- check all example files ---"
python3 src/yaml_toml_converter.py --check examples/config.yaml examples/config.toml examples/nested.yaml examples/nested.toml examples/lossy-dates.toml examples/lossy-null.yaml || true
echo "--- refuse: dates have no yaml form here ---"
python3 src/yaml_toml_converter.py --to yaml examples/lossy-dates.toml || true
echo "--- refuse: null has no toml form ---"
python3 src/yaml_toml_converter.py --to toml examples/lossy-null.yaml || true

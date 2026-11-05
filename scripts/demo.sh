#!/usr/bin/env bash
# Demo flow for the video: a real model judges the sample project, then PreJudge judges itself.
set -euo pipefail
if [ -n "${OPENMINION_CONFIG:-}" ]; then CONFIG="$OPENMINION_CONFIG"
elif [ -f ".openminion/agents.json" ]; then CONFIG="$(pwd)/.openminion/agents.json"
else CONFIG="$HOME/.openminion/agents.json"; fi
echo "Using OpenMinion config: $CONFIG"
LANG_ARG="${PREJUDGE_LANG:-en}"
TMP="$(mktemp -d)"
cp -r examples/sample-project "$TMP/sample-project"

echo "== 1. Sample project (has a committed fake key and no demo link)"
prejudge cortensor-hackathon-5 "$TMP/sample-project" --config "$CONFIG" --lang "$LANG_ARG" --fix --timeout 900 || true

echo "== 2. Second run shows the score delta"
prejudge cortensor-hackathon-5 "$TMP/sample-project" --config "$CONFIG" --lang "$LANG_ARG" --no-skeptic --timeout 900 || true

echo "== 3. PreJudge judges its own submission"
prejudge cortensor-hackathon-5 . --config "$CONFIG" --lang "$LANG_ARG" --timeout 900 || true
echo "Reports: $TMP/sample-project/.prejudge/ and ./.prejudge/"

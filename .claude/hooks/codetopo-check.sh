#!/bin/bash
# PostToolUse hook: run codetopo on edited Python files
# Stdin receives JSON with tool_input.file_path
# Stdout is injected back into Claude's context

INPUT=$(cat)
FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty')

# Only check Python files
if [[ "$FILE_PATH" != *.py ]]; then
  exit 0
fi

# Skip test fixtures and generated files
if [[ "$FILE_PATH" == *fixtures* ]] || [[ "$FILE_PATH" == *generated* ]] || [[ "$FILE_PATH" == *.fixed.py ]]; then
  exit 0
fi

# Find codetopo — check venv first, then PATH
CODETOPO=""
SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
PROJECT_DIR="${CLAUDE_PROJECT_DIR:-$(cd "$SCRIPT_DIR/../.." && pwd)}"
if [ -x "$PROJECT_DIR/.venv/bin/codetopo" ]; then
  CODETOPO="$PROJECT_DIR/.venv/bin/codetopo"
elif command -v codetopo &>/dev/null; then
  CODETOPO="codetopo"
else
  exit 0  # silently skip if not installed
fi

# Run check, capture output
RESULT=$($CODETOPO check "$FILE_PATH" --output json 2>/dev/null)
if [ $? -eq 0 ]; then
  exit 0  # no errors, nothing to report
fi

# Only report high-confidence findings: structural_duplication, circular_dependency
# Everything else is advisory noise that pollutes context.
BLOCKING_TYPES="structural_duplication|circular_dependency"
BLOCKING=$(echo "$RESULT" | jq -r "[.findings[] | select(.severity == \"error\" and (.type | test(\"$BLOCKING_TYPES\")))] | length" 2>/dev/null)
if [ "$BLOCKING" = "0" ] || [ -z "$BLOCKING" ]; then
  exit 0
fi

echo "codetopo: $BLOCKING structural error(s) in $FILE_PATH:"
echo "$RESULT" | jq -r ".findings[] | select(.severity == \"error\" and (.type | test(\"$BLOCKING_TYPES\"))) | \"  [\(.type)] \(.message)\n    Fix: \(.fix_suggestion)\"" 2>/dev/null

exit 0  # exit 0 = inject as context, don't block

#!/usr/bin/env bash
# Start the reconciliation. Extra arguments are passed through, e.g. ./scripts/run.sh --inspect
set -u
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"
cd "$PROJECT_ROOT" || exit 1

find_uv || fail "uv is not installed. Run ./scripts/setup.sh first (see README)."
[ -f .env ] || fail ".env not found. Run ./scripts/setup.sh, then fill in .env."
[ -d .venv ] || fail "Dependencies are not installed. Run ./scripts/setup.sh first."

"$UV" run python -m src.main --check-config >/dev/null || {
  "$UV" run python -m src.main --check-config
  fail "Fix the configuration problem above, then try again."
}

"$UV" run python -m src.main "$@"
status=$?
case $status in
  0) ;;
  1) echo "The run stopped safely. See the message above and logs/errors/ for details." ;;
  2) echo "Configuration problem - see the message above." ;;
  3) echo "Live mode is blocked until the Zoho screens are verified (see README)." ;;
  130) echo "Stopped with Ctrl+C." ;;
esac
exit $status

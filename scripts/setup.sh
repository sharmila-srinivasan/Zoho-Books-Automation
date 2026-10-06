#!/usr/bin/env bash
# One-time setup: checks tools, installs dependencies and the browser, checks .env, runs tests.
set -u
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"
cd "$PROJECT_ROOT" || exit 1

echo "========================================"
echo "ZOHO PAYMENT RECONCILIATION - SETUP"
echo "========================================"

echo "1. Checking uv"
find_uv || fail "uv is not installed. Install it (see README, step 2) and run this script again."
ok "uv found: $("$UV" --version)"

echo "2. Checking Python (uv installs it if missing)"
"$UV" python install 3.12 >/dev/null 2>&1 || fail "Could not install Python 3.12 with uv."
ok "Python: $("$UV" run --no-project python --version 2>&1)"

echo "3. Creating virtual environment and installing dependencies"
"$UV" sync || fail "Dependency installation failed (see messages above)."
ok "Dependencies installed in .venv"

echo "4. Installing Playwright Chromium browser"
"$UV" run playwright install chromium || fail "Browser installation failed."
ok "Playwright Chromium installed"

echo "5. Checking .env"
if [ ! -f .env ]; then
  cp .env.example .env
  warn "Created .env from .env.example - open it and fill in ZOHO_BASE_URL (and optionally your login)."
else
  ok ".env exists"
fi

echo "6. Validating configuration"
if "$UV" run python -m src.main --check-config; then
  ok "Configuration is valid"
  CONFIG_OK=1
else
  warn "Configuration needs attention (see message above), then run: ./scripts/run.sh"
  CONFIG_OK=0
fi

echo "7. Running tests"
"$UV" run pytest -q || fail "Some tests failed - do not use the tool until this is resolved."
ok "All tests passed"

echo "========================================"
if [ "$CONFIG_OK" = "1" ]; then
  echo "SETUP COMPLETE - start with: ./scripts/run.sh"
else
  echo "SETUP COMPLETE (edit .env before running) - then: ./scripts/run.sh"
fi
echo "========================================"

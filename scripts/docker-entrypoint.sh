#!/usr/bin/env bash
# Container startup: build .streamlit/secrets.toml from environment variables,
# then launch Streamlit. Secrets are injected by the hosting platform at runtime
# (Render/Railway/Cloud Run env vars) — they are NEVER baked into the image.
#
# Recognized env vars:
#   GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET  -> enables Google sign-in gate
#   COOKIE_SECRET     (optional)             -> session cookie key (auto-generated if absent)
#   APP_URL           (e.g. https://agent-aware.onrender.com)
#   REDIRECT_URI      (optional)             -> defaults to "$APP_URL/oauth2callback"
#   ALLOWED_EMAILS    (optional, CSV)        -> restrict access to these Gmail addresses
#   PORT              (optional)             -> hosting platforms inject this; defaults to 8501
set -euo pipefail

SECRETS_DIR=".streamlit"
SECRETS_FILE="$SECRETS_DIR/secrets.toml"
mkdir -p "$SECRETS_DIR"

if [[ -n "${GOOGLE_CLIENT_ID:-}" && -n "${GOOGLE_CLIENT_SECRET:-}" ]]; then
  COOKIE_SECRET="${COOKIE_SECRET:-$(python -c 'import secrets; print(secrets.token_hex(32))')}"

  # Derive the OAuth callback URL from APP_URL unless REDIRECT_URI is set explicitly.
  if [[ -z "${REDIRECT_URI:-}" ]]; then
    if [[ -n "${APP_URL:-}" ]]; then
      REDIRECT_URI="${APP_URL%/}/oauth2callback"
    else
      REDIRECT_URI="http://localhost:8501/oauth2callback"
    fi
  fi

  # Turn ALLOWED_EMAILS="a@x.com, b@y.com" into a TOML array; empty -> []
  ALLOW_TOML="[]"
  if [[ -n "${ALLOWED_EMAILS:-}" ]]; then
    ALLOW_TOML=$(python -c "import os,json; print(json.dumps([e.strip() for e in os.environ['ALLOWED_EMAILS'].split(',') if e.strip()]))")
  fi

  cat > "$SECRETS_FILE" <<EOF
[auth]
redirect_uri = "$REDIRECT_URI"
cookie_secret = "$COOKIE_SECRET"
client_id = "$GOOGLE_CLIENT_ID"
client_secret = "$GOOGLE_CLIENT_SECRET"
server_metadata_url = "https://accounts.google.com/.well-known/openid-configuration"

[auth_allowlist]
emails = $ALLOW_TOML
EOF
  echo "✅ Google sign-in ENABLED (redirect_uri=$REDIRECT_URI, allowlist=$ALLOW_TOML)"
else
  # No Google credentials -> open mode. Remove any stale secrets so the app
  # doesn't try to initialize a half-configured auth provider.
  rm -f "$SECRETS_FILE"
  echo "⚠ GOOGLE_CLIENT_ID not set — running in OPEN mode (no login required)."
fi

PORT="${PORT:-8501}"
echo "🚀 Starting Agent-Aware on port $PORT"
exec streamlit run frontend/app.py \
  --server.port "$PORT" \
  --server.address 0.0.0.0 \
  --server.headless true \
  --server.enableCORS false \
  --server.enableXsrfProtection true

#!/bin/zsh

set -u
cd "${0:A:h}"

GEMINI_KEYCHAIN_SERVICE="ppflow-gemini-api-key"
KEYCHAIN_ACCOUNT="${USER:-$(id -un)}"

if [[ -z "${GEMINI_API_KEY:-}" ]]; then
  gemini_key="$(
    security find-generic-password \
      -a "$KEYCHAIN_ACCOUNT" \
      -s "$GEMINI_KEYCHAIN_SERVICE" \
      -w 2>/dev/null || true
  )"

  if [[ -z "$gemini_key" ]]; then
    read -s "gemini_key?Gemini APIキー（初回のみ）: "
    echo
    if [[ -z "$gemini_key" ]]; then
      echo "APIキーが空のため起動を中止します。"
      exit 1
    fi

    security add-generic-password \
      -U \
      -a "$KEYCHAIN_ACCOUNT" \
      -s "$GEMINI_KEYCHAIN_SERVICE" \
      -w "$gemini_key" >/dev/null
    echo "Gemini APIキーをmacOSキーチェーンへ保存しました。"
  fi

  export GEMINI_API_KEY="$gemini_key"
fi

exec python3 -B web_app.py "$@"

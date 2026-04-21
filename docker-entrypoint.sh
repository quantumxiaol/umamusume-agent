#!/bin/sh
set -eu

TEMPLATE_PATH="${ENV_TEMPLATE_PATH:-.env.template}"
TARGET_PATH="${ENV_FILE_PATH:-.env}"

if [ ! -f "$TEMPLATE_PATH" ]; then
  echo "Missing env template: $TEMPLATE_PATH" >&2
  exit 1
fi

cp "$TEMPLATE_PATH" "$TARGET_PATH"

replace_placeholder() {
  var_name="$1"
  placeholder="$2"
  eval "value=\${$var_name-}"

  if [ -z "$value" ]; then
    echo "Missing required environment variable: $var_name" >&2
    exit 1
  fi

  escaped_value=$(printf '%s' "$value" | sed 's/[\/&]/\\&/g')
  sed -i.bak "s|$placeholder|$escaped_value|g" "$TARGET_PATH"
}

replace_placeholder "ROLEPLAY_LLM_MODEL_NAME" "<YOUR_MODEL_NAME_HERE>"
replace_placeholder "ROLEPLAY_LLM_MODEL_BASE_URL" "<YOUR_BASE_URL_HERE>"
replace_placeholder "ROLEPLAY_LLM_MODEL_API_KEY" "<YOUR_API_KEY_HERE>"
rm -f "${TARGET_PATH}.bak"

if [ "$#" -gt 0 ]; then
  exec "$@"
fi

exec uv run python app.py

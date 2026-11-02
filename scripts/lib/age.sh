#!/usr/bin/env bash
# Shared age identity resolution for every script in this repository.
#
# The identity is the same for the whole fleet and is never committed. The
# lookup is repository-first so a freshly cloned repo works as soon as the key
# is pasted into <repo>/.sops/keys.txt, with no dependency on the operator's
# home directory or on an ssh-agent style keyring.
#
# Chain:
#   1. $SOPS_AGE_KEY_FILE, when set and readable
#   2. <repo>/.sops/keys.txt
#   3. <repo>/age-key.txt        (legacy name, kept for compatibility)
#   4. $HOME/.config/sops/age/keys.txt
#
# Callers source this file and use age_key_file / require_age_key.

age_key_file() {
  local root="$1" candidate
  if [[ -n "${SOPS_AGE_KEY_FILE:-}" && -r "${SOPS_AGE_KEY_FILE}" ]]; then
    printf '%s\n' "$SOPS_AGE_KEY_FILE"
    return 0
  fi
  for candidate in "$root/.sops/keys.txt" "$root/age-key.txt" \
    "$HOME/.config/sops/age/keys.txt"; do
    if [[ -r "$candidate" ]]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  return 1
}

require_age_key() {
  local root="$1" file
  if ! file="$(age_key_file "$root")"; then
    {
      echo "No age identity found for $root." >&2
      echo "Paste the fleet key (same for every repository) in:" >&2
      echo "  $root/.sops/keys.txt" >&2
      echo "then: chmod 600 $root/.sops/keys.txt" >&2
      echo "The canonical copy lives in the password manager." >&2
    } >&2
    return 1
  fi
  chmod 600 "$file" 2>/dev/null || true
  export SOPS_AGE_KEY_FILE="$file"
  printf '%s\n' "$file"
}

require_sops() {
  command -v sops >/dev/null 2>&1 || {
    echo "sops not found. Install it: brew install sops" >&2
    return 1
  }
}

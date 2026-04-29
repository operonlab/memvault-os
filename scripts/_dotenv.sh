#!/usr/bin/env bash
# memvault-os — robust .env value reader
#
# WHY: doctor.sh used to inline `awk -F= '$1==KEY{print $2}'` to read
# MEMVAULT_LLM_DEFERRED. That mis-parses every realistic .env line shape:
#   * KEY=1 # comment    → captures "1 # comment"
#   * KEY="1"            → captures "\"1\""
#   * KEY='1'            → captures "'1'"
#   *  KEY = 1           → does not match KEY at all (whitespace around =)
#   * # KEY=1            → STILL matches the commented-out line
#
# read_dotenv_value normalises all of those to a bare value:
#   read_dotenv_value KEY [path]   → echoes the unquoted, comment-stripped value
#                                    (empty echo if missing or commented out)
#
# Implementation note: uses pure POSIX awk (no gawk-specific match() with array),
# so it works on macOS BSD awk + Linux gawk + busybox awk in CI.
# shellcheck shell=bash

read_dotenv_value() {
    local key="$1"
    local file="${2:-${REPO_ROOT:-.}/.env}"
    [[ -f "${file}" ]] || return 0
    awk -v key="${key}" '
        # Skip blank lines and whole-line comments (with or without leading ws).
        /^[[:space:]]*$/ { next }
        /^[[:space:]]*#/ { next }
        {
            line = $0
            # Find first `=`.
            eq = index(line, "=")
            if (eq == 0) next
            k = substr(line, 1, eq - 1)
            v = substr(line, eq + 1)
            # Trim whitespace around the key.
            sub(/^[[:space:]]+/, "", k)
            sub(/[[:space:]]+$/, "", k)
            if (k != key) next
            # Trim leading whitespace from value.
            sub(/^[[:space:]]+/, "", v)
            # Strip trailing inline comment ` # ...` (whitespace-prefixed `#` only,
            # so values like password#123 survive).
            sub(/[[:space:]]+#.*$/, "", v)
            # Trim trailing whitespace.
            sub(/[[:space:]]+$/, "", v)
            # Unwrap surrounding double or single quotes (one layer only).
            n = length(v)
            if (n >= 2) {
                first = substr(v, 1, 1)
                last  = substr(v, n, 1)
                if ((first == "\"" && last == "\"") || (first == "'\''" && last == "'\''")) {
                    v = substr(v, 2, n - 2)
                }
            }
            print v
            exit
        }
    ' "${file}"
}

# safe_source_dotenv [path]  — export every KEY=VALUE in the dotenv file
# WITHOUT subjecting values to shell expansion.
#
# WHY (codex slice 1 #5): the previous `set -a; source .env; set +a` pattern
# in scripts/_lib.sh treated every value as a shell expression. Any password
# or API key containing `$`, backtick, or `\` was either expanded
# unpredictably or rejected with `set -u`. This helper iterates the file
# line by line, normalises the value via read_dotenv_value (already handles
# quotes / inline comments / whitespace), and uses `export VAR="value"`
# with the value passed as a literal — bash's variable assignment rules do
# NOT re-expand the right-hand side of an `=` assignment.
safe_source_dotenv() {
    local file="${1:-${REPO_ROOT:-.}/.env}"
    [[ -f "${file}" ]] || return 0
    local key value line eq
    while IFS= read -r line || [[ -n "${line}" ]]; do
        # Skip blank lines and full-line comments.
        [[ "${line}" =~ ^[[:space:]]*$ ]] && continue
        [[ "${line}" =~ ^[[:space:]]*# ]] && continue
        eq="${line%%=*}"
        # Skip lines without an `=` (eq == line means no `=` was found).
        [[ "${eq}" == "${line}" ]] && continue
        # Trim whitespace around the key.
        key="${eq#"${eq%%[![:space:]]*}"}"
        key="${key%"${key##*[![:space:]]}"}"
        # Reject identifiers that aren't legal env-var names.
        [[ "${key}" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
        # Reuse read_dotenv_value for value normalisation (quotes, comments,
        # whitespace) — slower than a single pass but the .env files are
        # small (< 200 lines) and we get the exact same parser.
        value="$(read_dotenv_value "${key}" "${file}")"
        # `printf -v` assigns without expanding the value.
        printf -v "${key}" '%s' "${value}"
        export "${key?}"
    done <"${file}"
}

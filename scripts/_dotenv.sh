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

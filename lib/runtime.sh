#!/usr/bin/env bash

minimal_whisper_resolve_python() {
    local candidate
    local -a candidates=()
    [[ -n "${MINIMAL_WHISPER_PYTHON:-}" ]] && candidates+=("$MINIMAL_WHISPER_PYTHON")
    candidates+=("${XDG_DATA_HOME:-$HOME/.local/share}/minimal-whisper/venv/bin/python")
    candidates+=("$HOME/.local/opt/openai-whisper/bin/python")
    if command -v python3 >/dev/null 2>&1; then
        candidates+=("$(command -v python3)")
    fi
    for candidate in "${candidates[@]}"; do
        [[ -x "$candidate" ]] || continue
        if "$candidate" -c 'import whisper, Xlib' >/dev/null 2>&1; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done
    return 1
}

minimal_whisper_resolve_ui_python() {
    local candidate
    local -a candidates=()
    [[ -n "${MINIMAL_WHISPER_UI_PYTHON:-}" ]] && candidates+=("$MINIMAL_WHISPER_UI_PYTHON")
    candidates+=("${XDG_DATA_HOME:-$HOME/.local/share}/minimal-whisper/venv/bin/python")
    if command -v python3 >/dev/null 2>&1; then
        candidates+=("$(command -v python3)")
    fi
    for candidate in "${candidates[@]}"; do
        [[ -x "$candidate" ]] || continue
        if "$candidate" -c 'import PySide6' >/dev/null 2>&1; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done
    return 1
}

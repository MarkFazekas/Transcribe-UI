#!/usr/bin/env python3
"""Claude Code hook: require a sub-agent code review before a turn that changed files ends.

Modes:
    start  - UserPromptSubmit: record a fingerprint of the repository as the turn baseline.
    stop   - Stop: if the repository differs from the baseline and this change-set was not
             reviewed yet, ask Claude to run the `code-reviewer` sub-agent before finishing.

The fingerprint covers HEAD (so commits made during the turn count as changes), the status
and the contents of every changed file. State lives in a private directory under the OS temp
dir keyed by session id, so the repo is never dirtied. Stdlib only, so it runs without the
project environment. Any unexpected failure lets Claude stop normally instead of erroring.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

MAX_LISTED_PATHS = 50
# Files above this size are fingerprinted by size and mtime instead of content.
MAX_HASHED_FILE_BYTES = 5 * 1024 * 1024
UNSAFE_ID_CHARS = re.compile(r"[^A-Za-z0-9_-]")
STATE_DIR_MODE = 0o700


class _Repo:
    """Minimal read-only view of the git repository containing `cwd`."""

    def __init__(self, cwd: str) -> None:
        self._cwd = cwd
        self.root = Path(self._git("rev-parse", "--show-toplevel").strip())

    def head(self) -> str:
        return self._git("rev-parse", "--verify", "--quiet", "HEAD").strip()

    def changed_paths(self, base_head: str | None) -> list[str]:
        """List paths changed since `base_head` (or HEAD) plus uncommitted and untracked ones."""
        diff_args = ["diff", "--name-only", "-z"]
        if base_head:
            diff_args.append(base_head)
        tracked = self._git(*diff_args, "HEAD") if base_head else ""
        working = self._git(*diff_args)
        untracked = self._git("ls-files", "--others", "--exclude-standard", "-z")
        names = f"{tracked}\0{working}\0{untracked}".split("\0")
        return sorted({name for name in names if name})

    def _git(self, *args: str) -> str:
        completed = subprocess.run(
            ["git", *args],
            cwd=self._cwd,
            capture_output=True,
            check=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        return completed.stdout


def _file_token(file_path: Path) -> bytes:
    """Return the file content, or its size and mtime when the file is too large to hash."""
    file_stat = file_path.stat()
    if file_stat.st_size > MAX_HASHED_FILE_BYTES:
        return f"{file_stat.st_size}:{file_stat.st_mtime_ns}".encode()
    return file_path.read_bytes()


def _fingerprint(repo: _Repo, head: str, paths: list[str]) -> str:
    digest = hashlib.sha256(head.encode())
    for path in paths:
        digest.update(path.encode())
        file_path = repo.root / path
        try:
            digest.update(_file_token(file_path))
        except OSError:
            digest.update(b"<missing>")
    return digest.hexdigest()


def _state_file(session_id: str) -> Path:
    directory = Path(tempfile.gettempdir()) / "claude-review-gate"
    directory.mkdir(mode=STATE_DIR_MODE, exist_ok=True)
    safe_id = UNSAFE_ID_CHARS.sub("_", session_id) or "default"
    return directory / f"{safe_id}.json"


def _load_state(state_file: Path) -> dict[str, Any]:
    try:
        loaded = json.loads(state_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _save_state(state_file: Path, state: dict[str, Any]) -> None:
    temp_file = state_file.with_suffix(".tmp")
    temp_file.write_text(json.dumps(state), encoding="utf-8")
    temp_file.replace(state_file)


def _review_instruction(paths: list[str]) -> str:
    shown_paths = paths[:MAX_LISTED_PATHS]
    listed = "\n".join(f"- {path}" for path in shown_paths)
    hidden_count = len(paths) - MAX_LISTED_PATHS
    more = ""
    if hidden_count > 0:
        more = f"\n(+{hidden_count} more)"
    return (
        "Files changed this turn. Before finishing, get an independent review:\n"
        "1. Use the Agent tool with subagent_type `code-reviewer`. In the prompt, list the "
        "changed files below, say what the change was meant to do, and paste `git diff` for "
        "modified tracked files (the reviewer has no shell).\n"
        "2. Address every high/medium finding (fix it, or briefly justify why not).\n"
        "3. Then give the final answer with a one-line review summary. "
        "Do not run the review again after fixing.\n\n"
        f"Changed files since the turn started:\n{listed}{more}"
    )


def _run(mode: str, hook_input: dict[str, Any]) -> str | None:
    """Update the session state and return the review instruction when one is due."""
    cwd = hook_input.get("cwd") or os.environ.get("CLAUDE_PROJECT_DIR", ".")
    repo = _Repo(str(cwd))
    state_file = _state_file(str(hook_input.get("session_id", "")))
    state = _load_state(state_file)
    head = repo.head()
    if mode == "start":
        state["base_head"] = head
        state["baseline"] = _fingerprint(repo, head, repo.changed_paths(None))
        _save_state(state_file, state)
        return None

    paths = repo.changed_paths(state.get("base_head"))
    fingerprint = _fingerprint(repo, head, repo.changed_paths(None))
    if hook_input.get("stop_hook_active"):
        return None
    if fingerprint in {state.get("baseline"), state.get("reviewed")}:
        return None
    state["reviewed"] = fingerprint
    _save_state(state_file, state)
    return _review_instruction(paths)


def _read_hook_input() -> dict[str, Any]:
    hook_input = json.load(sys.stdin)
    return hook_input if isinstance(hook_input, dict) else {}


def main() -> int:
    """Run the hook in the mode given as the first CLI argument; never fail the session."""
    mode = "stop"
    if len(sys.argv) > 1:
        mode = sys.argv[1]
    try:
        instruction = _run(mode, _read_hook_input())
    except (OSError, ValueError, subprocess.CalledProcessError):
        return 0
    if instruction:
        context = {"hookEventName": "Stop", "additionalContext": instruction}
        print(json.dumps({"hookSpecificOutput": context}))
    return 0


if __name__ == "__main__":
    sys.exit(main())

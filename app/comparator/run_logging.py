#!/usr/bin/env python3
"""Utilities for capturing script output and run metadata."""

from __future__ import annotations

import atexit
import shlex
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import TextIO


class Tee:
    def __init__(self, *streams: TextIO) -> None:
        self._streams = streams

    def write(self, data: str) -> int:
        for stream in self._streams:
            stream.write(data)
        return len(data)

    def flush(self) -> None:
        for stream in self._streams:
            stream.flush()

    def isatty(self) -> bool:
        for stream in self._streams:
            isatty = getattr(stream, "isatty", None)
            if callable(isatty) and isatty():
                return True
        return False

    @property
    def encoding(self) -> str | None:
        for stream in self._streams:
            encoding = getattr(stream, "encoding", None)
            if encoding:
                return encoding
        return None


def setup_run_logging(output_dir: Path, config_path: Path | None = None) -> Path:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    log_path = output_dir / "run.log"
    log_handle = log_path.open("w", encoding="utf-8", buffering=1)

    original_stdout = sys.stdout
    original_stderr = sys.stderr
    sys.stdout = Tee(sys.stdout, log_handle)
    sys.stderr = Tee(sys.stderr, log_handle)

    def _cleanup():
        sys.stdout = original_stdout
        sys.stderr = original_stderr
        try:
            log_handle.flush()
            log_handle.close()
        except OSError:
            pass

    atexit.register(_cleanup)

    command_line = shlex.join(sys.argv)
    (output_dir / "command.txt").write_text(f"{command_line}\n", encoding="utf-8")

    print(f"Command: {command_line}")
    print(f"Started: {datetime.now(tz=timezone.utc).isoformat(timespec='seconds')}")

    if config_path:
        config_path = Path(config_path)
        if config_path.exists():
            suffix = config_path.suffix if config_path.suffix else ".yaml"
            target = output_dir / f"config_used{suffix}"
            shutil.copy2(config_path, target)
            print(f"Config copied to: {target}")
        else:
            print(f"Warning: config file not found: {config_path}")

    return log_path

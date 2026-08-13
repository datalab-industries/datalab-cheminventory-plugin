"""Logging configuration for the cheminventory/datalab sync.

The formatting deliberately mirrors `pydatalab.logger`, so that plugin logs and
server logs look the same when read side-by-side; the request ID column is
dropped, as this package only ever runs as a one-shot CLI process.

The conventions used across this package are:

- `ERROR`: an item or container failed to sync; the sync continues.
- `WARN`: an item was deliberately skipped, or the two databases disagree in a
  way that may need a human to resolve.
- `INFO`: something actually changed, i.e., an item was created, updated or
  disposed in datalab, or a container was added or deleted in cheminventory,
  plus the per-run summaries.
- `DEBUG`: everything else; per-item scanning, file downloads and uploads,
  no-op skips and raw API traffic.

A run that changes nothing should therefore emit nothing above `DEBUG`, aside
from the summary lines.
"""

import logging
import os
import pathlib
from functools import cache

from rich.console import Console
from rich.text import Text

__all__ = ("CONSOLE", "DEFAULT_LOG_LEVEL", "LOGGER", "LOG_LEVEL_ENV_VAR", "setup_logging")

# Abbreviate the two long level names so that all levels fit in 5 characters
logging.addLevelName(logging.WARNING, "WARN")
logging.addLevelName(logging.CRITICAL, "CRIT")

LOG_FORMAT_STRING = "%(asctime)s %(levelname)-5s │ %(message)s (PID: %(process)d - %(source)s)"

DEFAULT_LOG_LEVEL = "INFO"
"""The level used if neither the CLI nor `$CHEMINVENTORY_SYNC_LOG_LEVEL` set one."""

LOG_LEVEL_ENV_VAR = "CHEMINVENTORY_SYNC_LOG_LEVEL"


@cache
def _module_from_path(pathname: str) -> str:
    """Resolve a source file path to a dotted module name by walking up
    the package tree, e.g., '.../src/pydatalab/main.py' -> 'pydatalab.main'."""
    path = pathlib.Path(pathname)
    parts = [path.stem]
    parent = path.parent
    while (parent / "__init__.py").exists():
        parts.append(parent.name)
        parent = parent.parent
    return ".".join(reversed(parts))


class LogContextFilter(logging.Filter):
    """Stamps each record with a concise 'module:function:lineno' source location."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.source = f"{_module_from_path(record.pathname)}:{record.funcName}:{record.lineno}"
        return True


class AnsiColorFormatter(logging.Formatter):
    """Truncating formatter that renders the timestamp and PID/source suffix in
    grey and the level name in a severity-dependent colour, leaving the message
    itself unstyled."""

    GREY = "\x1b[90m"
    RESET = "\x1b[0m"

    LOGLEVEL_COLORS = {
        logging.DEBUG: "36m",
        logging.INFO: "32m",
        logging.WARNING: "33m",
        logging.ERROR: "1;91m",
        logging.CRITICAL: "1;31m",
    }

    max_width = 2000

    def __init__(self, fmt: str = LOG_FORMAT_STRING):
        super().__init__(fmt)
        self._level_formatters = {}
        for level, color in self.LOGLEVEL_COLORS.items():
            # As each level gets its own formatter, the level name can be
            # coloured and padded statically, keeping any background
            # colour off the padding spaces
            level_name = logging.getLevelName(level)
            padding = " " * max(0, 5 - len(level_name))
            colored_fmt = (
                fmt.replace("%(asctime)s", f"{self.GREY}%(asctime)s{self.RESET}")
                .replace("%(levelname)-5s", f"\x1b[{color}{level_name}{self.RESET}{padding}")
                .replace("│", f"\x1b[{color}│{self.RESET}")
                .replace("(PID:", f"{self.GREY}(PID:")
            )
            if "(PID:" in fmt:
                colored_fmt += self.RESET
            self._level_formatters[level] = logging.Formatter(colored_fmt)

    def format(self, record: logging.LogRecord) -> str:
        formatter = self._level_formatters.get(record.levelno)
        message = formatter.format(record) if formatter else super().format(record)
        if len(message) > self.max_width:
            message = message[: self.max_width] + "[...]"
        return message


CONSOLE = Console(stderr=True)
"""The console shared by the log handler and every progress bar in this package.

Progress bars are driven by a `rich.live.Live` bound to this console; anything
printed through the same console is emitted *above* the live region, so log
lines and the bar no longer overwrite one another. Both go to stderr, leaving
stdout free for report output (e.g., the `status` subcommand).
"""


class RichConsoleHandler(logging.Handler):
    """Emits records through a shared `rich` console rather than writing to the
    stream directly, so that a live progress bar is redrawn beneath each line.

    The formatter still produces ANSI-coloured text, which is parsed back into
    rich's own styling; a side benefit is that colour is dropped automatically
    when stderr is not a terminal, e.g., when the sync runs from cron.
    """

    def __init__(self, console: Console, level: int = logging.NOTSET):
        super().__init__(level=level)
        self.console = console

    def emit(self, record: logging.LogRecord) -> None:
        try:
            # `soft_wrap` keeps rich from re-wrapping long lines to the
            # terminal width, matching plain `StreamHandler` behaviour
            self.console.print(Text.from_ansi(self.format(record)), soft_wrap=True)
        except Exception:
            self.handleError(record)


LOGGER = logging.getLogger("datalab_cheminventory_plugin")
"""The logger used by every module in this package."""


def _coerce_level(level: int | str | None) -> int:
    """Turn a level name or number (or `None`, meaning consult the environment)
    into a numeric logging level.
    """
    if level is None:
        level = os.getenv(LOG_LEVEL_ENV_VAR, DEFAULT_LOG_LEVEL)
    if isinstance(level, int):
        return level

    try:
        return logging.getLevelNamesMapping()[level.strip().upper()]
    except KeyError:
        raise ValueError(f"Unknown log level {level!r}.")


def setup_logging(level: int | str | None = None) -> logging.Logger:
    """Attach a coloured handler to the package logger and set its level.

    Only the CLI entrypoint should call this; importing this package on its own
    leaves log handling to the consuming application.

    Parameters:
        level: The level to log at, as a name or number. If `None`, the
            `$CHEMINVENTORY_SYNC_LOG_LEVEL` environment variable is used,
            falling back to `DEFAULT_LOG_LEVEL`.

    Returns:
        The configured package logger.

    """
    numeric_level = _coerce_level(level)

    if not LOGGER.handlers:
        handler = RichConsoleHandler(CONSOLE)
        handler.setFormatter(AnsiColorFormatter())
        handler.addFilter(LogContextFilter())
        LOGGER.addHandler(handler)
        LOGGER.propagate = False

    LOGGER.setLevel(numeric_level)
    return LOGGER

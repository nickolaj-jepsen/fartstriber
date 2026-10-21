"""The base of every error that says what to do next. `livery` prints its message, without a traceback,
and exits with its `exit_code`; any other exception is a bug and keeps its traceback."""


class LiveryError(Exception):
    exit_code = 1


class UsageError(LiveryError):
    """A bad argument: exit 2, like the CLI's own usage errors."""

    exit_code = 2

"""State-owned study timezone, scoped to a call (no global process mutation)."""
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import timedelta, timezone
from functools import wraps
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_NAME = "Asia/Hong_Kong"
_name = ContextVar("study_timezone_name", default=DEFAULT_NAME)


def name():
    return _name.get()


def local_zone():
    value = name()
    try:
        return ZoneInfo(value)
    except ZoneInfoNotFoundError as error:
        # Default Windows installation can work without optional tzdata.
        if value in {"Asia/Hong_Kong", "Asia/Shanghai"}:
            return timezone(timedelta(hours=8))
        if value in {"UTC", "Etc/UTC"}:
            return timezone.utc
        raise ValueError("时区不可用：请安装 tzdata 或核对 state.timezone") from error


@contextmanager
def using_state(state):
    value = state.get("timezone") or name()
    if not isinstance(value, str):
        raise ValueError("state.timezone 必须为 IANA 时区名称")
    token = _name.set(value)
    try:
        local_zone()
        yield
    finally:
        _name.reset(token)


def state_timezone(function):
    @wraps(function)
    def call(state, *args, **kwargs):
        with using_state(state):
            return function(state, *args, **kwargs)
    return call

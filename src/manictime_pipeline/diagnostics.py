"""Attach CLI target context without changing exception types or return contracts."""

from contextlib import contextmanager


def target_error(exc, path, role="target"):
    if not getattr(exc, "cli_targets", None):
        exc.cli_targets = [{"role": role, "path": str(path)}]
    return exc


@contextmanager
def error_target(path, role="target"):
    try:
        yield
    except (Exception, KeyboardInterrupt) as exc:
        target_error(exc, path, role)
        raise


@contextmanager
def progress_items(items):
    """Retain known progress when a multi-item operation raises; no rollback semantics."""
    try:
        yield
    except (Exception, KeyboardInterrupt) as exc:
        if items:
            exc.cli_processed_items = list(items)
        raise

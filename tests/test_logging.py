import io
import logging

import pytest

from manictime_pipeline.export import CsvEncoder, decode_cell, encode_cell
from manictime_pipeline.logging_utils import SUCCESS, ColorFormatter, configure, supports_color


@pytest.mark.parametrize("value", [None, "", r"\N", r"\Btest", r"\\text", b"", b"\0\xff", "日本語"])
def test_lossless_cells(value):
    assert decode_cell(encode_cell(value)) == value


def test_csv_long_multiline_unicode():
    text = '題名,"\n' + "x" * 200000
    assert CsvEncoder().row([text]).decode("utf-8").startswith('"題名,""')


@pytest.mark.parametrize(
    "level,color",
    [(SUCCESS, "\033[32m"), (logging.ERROR, "\033[31m"), (logging.CRITICAL, "\033[31m")],
)
def test_level_colors(level, color):
    record = logging.LogRecord("test", level, "", 0, "hello", (), None)
    assert ColorFormatter(True).format(record).startswith(color + "[")
    assert "\033" not in ColorFormatter(False).format(record)


def test_success_level():
    assert logging.INFO < SUCCESS < logging.WARNING
    assert logging.getLevelName(SUCCESS) == "SUCCESS"


def test_no_color_for_non_tty_or_environment(monkeypatch):
    assert not supports_color(io.StringIO())

    class Tty:
        def isatty(self):
            return True

    monkeypatch.setenv("NO_COLOR", "")
    assert not supports_color(Tty())
    monkeypatch.delenv("NO_COLOR")
    monkeypatch.setenv("TERM", "dumb")
    assert not supports_color(Tty())


@pytest.mark.parametrize(
    "quiet,verbose,expected",
    [
        (False, False, ["INFO", "SUCCESS", "ERROR"]),
        (True, False, ["ERROR"]),
        (False, True, ["DEBUG", "INFO", "SUCCESS", "ERROR"]),
    ],
)
def test_logging_levels_and_routing(capsys, quiet, verbose, expected):
    configure(quiet, verbose)
    for level in [logging.DEBUG, logging.INFO, SUCCESS, logging.ERROR]:
        logging.log(level, "message")
    captured = capsys.readouterr()
    assert captured.out == ""
    assert [line.split("]")[0][1:] for line in captured.err.splitlines()] == expected


@pytest.mark.parametrize("text", ["a\rb", "a\r\nb", "a\nb"])
def test_csv_preserves_all_newline_styles(text):
    import csv

    encoded = CsvEncoder().row([text, "next"])
    assert list(csv.reader(io.StringIO(encoded.decode("utf-8"), newline=""))) == [[text, "next"]]

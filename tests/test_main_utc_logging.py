from __future__ import annotations

import logging
import time

from alan import __main__ as main_mod


def test_configure_logging_sets_gmtime_converter() -> None:
    original = logging.Formatter.converter
    original_level = logging.getLogger("alan").level
    try:
        logging.Formatter.converter = time.localtime
        main_mod._configure_logging()  # noqa: SLF001
        assert logging.Formatter.converter is time.gmtime
    finally:
        logging.Formatter.converter = original
        logging.getLogger("alan").setLevel(original_level)


def test_configure_logging_formats_record_in_utc() -> None:
    original = logging.Formatter.converter
    original_level = logging.getLogger("alan").level
    try:
        main_mod._configure_logging()  # noqa: SLF001
        formatter = logging.Formatter("%(asctime)s", datefmt="%Y-%m-%d %H:%M:%S")
        record = logging.LogRecord(
            "alan", logging.INFO, __file__, 1, "msg", None, None,
        )
        record.created = 1_700_000_000.0
        formatted = formatter.formatTime(record, "%Y-%m-%d %H:%M:%S")
        expected = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(1_700_000_000.0))
        assert formatted == expected
    finally:
        logging.Formatter.converter = original
        logging.getLogger("alan").setLevel(original_level)

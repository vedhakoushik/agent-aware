"""Shared pytest setup. Windows consoles default to cp1252, which can't encode ₹/emoji
that show up throughout this codebase's log lines and print() calls — without this,
any test that prints such a string crashes with UnicodeEncodeError unrelated to the
actual assertion being tested (see tests/test_prompt_injection_live.py history)."""
import io
import sys

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

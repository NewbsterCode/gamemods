"""Shared test setup.

A modal message box has nobody to click it in an unattended run (CI, or a
headless session), so it would block the suite forever. Make every dialog
fail the test immediately instead, naming the dialog, so an unexpected
prompt shows up as a readable failure rather than a hang.
"""

import pytest


class UnexpectedDialog(AssertionError):
    pass


@pytest.fixture(autouse=True)
def _no_modal_dialogs(monkeypatch):
    try:
        from tkinter import messagebox
    except ImportError:  # no Tk: the UI tests skip themselves
        yield
        return

    def _fail(kind):
        def dialog(title=None, message=None, **_kwargs):
            raise UnexpectedDialog(f"unexpected {kind} dialog: {title!r}: {message!r}")

        return dialog

    for name in ("showinfo", "showwarning", "showerror", "askyesno", "askokcancel",
                 "askyesnocancel", "askretrycancel", "askquestion"):
        monkeypatch.setattr(messagebox, name, _fail(name))
    yield

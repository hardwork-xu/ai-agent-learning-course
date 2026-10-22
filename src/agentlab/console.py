"""UTF-8 output for command-line entry points, including redirected Windows IO."""
import sys


def configure_utf8_output() -> None:
    """Configure CLI-owned streams without replacing capture streams such as StringIO.

    Call explicitly from main(), never during module import or a library operation.
    Reconfiguring TextIOWrapper also makes redirected output consistently UTF-8;
    no terminal code page or PYTHONIOENCODING setting is required from students.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="backslashreplace")

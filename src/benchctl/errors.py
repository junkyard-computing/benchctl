"""Typed exceptions, split by how the orchestrator must treat them.

- ``Refusal``        — a precondition failed; benchctl declined to act (safe, no device touched).
- ``CommandError``   — a driven tool (pixel-bootctl/pixel-ota/ssh) returned non-zero.
- ``UartTimeout``    — a ``uart wait``/``--expect`` timed out.
- ``Unrecoverable``  — recovery exhausted (rollback + power-cycle) and home base did not return.
"""

from __future__ import annotations


class BenchctlError(Exception):
    """Base for all benchctl errors."""


class Refusal(BenchctlError):
    """A safety precondition failed; benchctl refused to proceed."""


class CommandError(BenchctlError):
    """A driven command returned a non-zero exit status."""

    def __init__(self, argv, returncode: int, stderr: str = "") -> None:
        self.argv = list(argv)
        self.returncode = returncode
        self.stderr = stderr
        super().__init__(
            f"command {' '.join(self.argv)!r} exited {returncode}: {stderr.strip()}"
        )


class UartTimeout(BenchctlError):
    """A uart wait/expect did not match within the timeout."""


class Unrecoverable(BenchctlError):
    """Home base did not return after rollback and the power-cycle backstop."""

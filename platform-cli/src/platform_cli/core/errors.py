"""Core platform CLI errors.

NOTE: Architectural rules in ARCHITECTURE.md — do not refactor cross-layer.
"""

class PlatformError(Exception):
    """
    Domain-level error for the platform CLI.
    Carries a human-friendly message + optional code/reason/alert flag.
    """

    def __init__(
        self,
        message: str,
        code: str | None = None,
        reason: str | None = None,
        alert: bool = False,
    ) -> None:
        super().__init__(message)
        self.message = message      # <- this is what _run() expects
        self.code = code
        self.reason = reason
        self.alert = alert

    def __str__(self) -> str:
        return self.message

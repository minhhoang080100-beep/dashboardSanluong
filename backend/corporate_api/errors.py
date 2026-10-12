class CorporateError(Exception):
    """A public, deliberately non-sensitive integration error."""

    def __init__(self, status, code, message, *, retry_after=None, diagnostics=None):
        self.status = status
        self.code = code
        self.message = message
        self.retry_after = retry_after
        # Request-local private context; public routes serialize only code/message.
        self.diagnostics = diagnostics
        super().__init__(message)

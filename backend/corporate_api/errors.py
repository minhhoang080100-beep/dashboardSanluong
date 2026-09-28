class CorporateError(Exception):
    """A public, deliberately non-sensitive integration error."""

    def __init__(self, status, code, message, *, retry_after=None):
        self.status = status
        self.code = code
        self.message = message
        self.retry_after = retry_after
        super().__init__(message)

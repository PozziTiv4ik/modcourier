class CourierError(Exception):
    """A user-facing error with a stable machine-readable code."""

    def __init__(self, code: str, message: str, *, uncertain: bool = False):
        super().__init__(message)
        self.code = code
        self.uncertain = uncertain

    def as_dict(self):
        return {"code": self.code, "message": str(self), "uncertain": self.uncertain}

"""Structured error hierarchy — WorkshopError and subclasses."""


class WorkshopError(Exception):
    status_code: int = 500
    code: str = "system.internal_error"
    module: str | None = None

    def __init__(self, detail: str, *, code: str | None = None, module: str | None = None):
        self.detail = detail
        if code:
            self.code = code
        if module:
            self.module = module
        super().__init__(detail)


class NotFoundError(WorkshopError):
    status_code = 404
    code = "system.not_found"


class ForbiddenError(WorkshopError):
    status_code = 403
    code = "system.forbidden"


class ConflictError(WorkshopError):
    status_code = 409
    code = "system.conflict"


class BadRequestError(WorkshopError):
    status_code = 400
    code = "system.bad_request"


class RateLimitError(WorkshopError):
    status_code = 429
    code = "system.rate_limited"

from fastapi import HTTPException, status


class ValidationException(HTTPException):
    def __init__(self, detail: str, field: str = None):
        self.field = field
        super().__init__(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error_type": "validation_error",
                "message": detail,
                "field": field
            }
        )


class StatusConflictException(HTTPException):
    def __init__(self, detail: str, current_status: str = None, expected_status: str = None):
        super().__init__(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error_type": "status_conflict",
                "message": detail,
                "current_status": current_status,
                "expected_status": expected_status
            }
        )


class NotFoundException(HTTPException):
    def __init__(self, resource: str, identifier: str = None):
        msg = f"{resource} not found"
        if identifier:
            msg += f": {identifier}"
        super().__init__(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error_type": "not_found",
                "message": msg
            }
        )

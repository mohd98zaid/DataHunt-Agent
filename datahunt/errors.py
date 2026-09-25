import random
from enum import Enum
from typing import Optional, Dict, Any

class ErrorCategory(str, Enum):
    USER = "user"
    POLICY = "policy"
    BUDGET = "budget"
    TRANSIENT = "transient"
    CONFIGURATION = "configuration"
    MODEL = "model"
    SOURCE = "source"
    QUALITY = "quality"
    DATA = "data"
    SYSTEM = "system"

class ErrorCode(str, Enum):
    INVALID_REQUEST = "INVALID_REQUEST"
    POLICY_REFUSED = "POLICY_REFUSED"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    MODEL_RATE_LIMITED = "MODEL_RATE_LIMITED"
    MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"
    MODEL_AUTH_FAILED = "MODEL_AUTH_FAILED"
    MODEL_SCHEMA_INVALID = "MODEL_SCHEMA_INVALID"
    TOOL_NOT_ALLOWED = "TOOL_NOT_ALLOWED"
    SEARCH_RATE_LIMITED = "SEARCH_RATE_LIMITED"
    SEARCH_UNAVAILABLE = "SEARCH_UNAVAILABLE"
    FETCH_TIMEOUT = "FETCH_TIMEOUT"
    FETCH_DNS_FAILED = "FETCH_DNS_FAILED"
    FETCH_BLOCKED = "FETCH_BLOCKED"
    FETCH_TOO_LARGE = "FETCH_TOO_LARGE"
    FETCH_UNSUPPORTED_TYPE = "FETCH_UNSUPPORTED_TYPE"
    PARSE_FAILED = "PARSE_FAILED"
    EXTRACTION_EMPTY = "EXTRACTION_EMPTY"
    VERIFICATION_CONFLICT = "VERIFICATION_CONFLICT"
    DUPLICATE_CONFLICT = "DUPLICATE_CONFLICT"
    DB_BUSY = "DB_BUSY"
    DB_CONSTRAINT = "DB_CONSTRAINT"
    EXPORT_FAILED = "EXPORT_FAILED"
    CANCELLED = "CANCELLED"
    INTERNAL_ERROR = "INTERNAL_ERROR"

ERROR_METADATA: Dict[ErrorCode, Dict[str, Any]] = {
    ErrorCode.INVALID_REQUEST: {"category": ErrorCategory.USER, "retryable": False},
    ErrorCode.POLICY_REFUSED: {"category": ErrorCategory.POLICY, "retryable": False},
    ErrorCode.BUDGET_EXCEEDED: {"category": ErrorCategory.BUDGET, "retryable": False},
    ErrorCode.MODEL_RATE_LIMITED: {"category": ErrorCategory.TRANSIENT, "retryable": True},
    ErrorCode.MODEL_UNAVAILABLE: {"category": ErrorCategory.TRANSIENT, "retryable": True},
    ErrorCode.MODEL_AUTH_FAILED: {"category": ErrorCategory.CONFIGURATION, "retryable": False},
    ErrorCode.MODEL_SCHEMA_INVALID: {"category": ErrorCategory.MODEL, "retryable": True},
    ErrorCode.TOOL_NOT_ALLOWED: {"category": ErrorCategory.POLICY, "retryable": False},
    ErrorCode.SEARCH_RATE_LIMITED: {"category": ErrorCategory.TRANSIENT, "retryable": True},
    ErrorCode.SEARCH_UNAVAILABLE: {"category": ErrorCategory.TRANSIENT, "retryable": True},
    ErrorCode.FETCH_TIMEOUT: {"category": ErrorCategory.TRANSIENT, "retryable": True},
    ErrorCode.FETCH_DNS_FAILED: {"category": ErrorCategory.TRANSIENT, "retryable": False},
    ErrorCode.FETCH_BLOCKED: {"category": ErrorCategory.POLICY, "retryable": False},
    ErrorCode.FETCH_TOO_LARGE: {"category": ErrorCategory.SOURCE, "retryable": False},
    ErrorCode.FETCH_UNSUPPORTED_TYPE: {"category": ErrorCategory.SOURCE, "retryable": False},
    ErrorCode.PARSE_FAILED: {"category": ErrorCategory.SOURCE, "retryable": True},
    ErrorCode.EXTRACTION_EMPTY: {"category": ErrorCategory.QUALITY, "retryable": False},
    ErrorCode.VERIFICATION_CONFLICT: {"category": ErrorCategory.QUALITY, "retryable": False},
    ErrorCode.DUPLICATE_CONFLICT: {"category": ErrorCategory.QUALITY, "retryable": False},
    ErrorCode.DB_BUSY: {"category": ErrorCategory.TRANSIENT, "retryable": True},
    ErrorCode.DB_CONSTRAINT: {"category": ErrorCategory.DATA, "retryable": False},
    ErrorCode.EXPORT_FAILED: {"category": ErrorCategory.TRANSIENT, "retryable": True},
    ErrorCode.CANCELLED: {"category": ErrorCategory.USER, "retryable": False},
    ErrorCode.INTERNAL_ERROR: {"category": ErrorCategory.SYSTEM, "retryable": False},
}

class DataHuntError(Exception):
    """Standard unified error envelope for DataHunt agent."""
    def __init__(
        self,
        code: ErrorCode,
        user_message: str,
        operator_message: Optional[str] = None,
        run_id: Optional[str] = None,
        tool: Optional[str] = None,
        attempt: int = 1,
        retry_after_seconds: Optional[float] = None,
        cause_id: Optional[str] = None,
        retryable: Optional[bool] = None,
        category: Optional[ErrorCategory] = None,
    ):
        super().__init__(user_message)
        self.code = code
        meta = ERROR_METADATA.get(code, {"category": ErrorCategory.SYSTEM, "retryable": False})
        self.category = category or meta["category"]
        self.retryable = retryable if retryable is not None else meta["retryable"]
        self.user_message = user_message
        self.operator_message = operator_message or user_message
        self.run_id = run_id
        self.tool = tool
        self.attempt = attempt
        self.retry_after_seconds = retry_after_seconds
        self.cause_id = cause_id

    def to_dict(self, include_operator: bool = False) -> Dict[str, Any]:
        d = {
            "code": self.code.value,
            "category": self.category.value,
            "retryable": self.retryable,
            "user_message": self.user_message,
            "run_id": self.run_id,
            "tool": self.tool,
            "attempt": self.attempt,
            "retry_after_seconds": self.retry_after_seconds,
            "cause_id": self.cause_id,
        }
        if include_operator:
            d["operator_message"] = self.operator_message
        return d

def compute_backoff(
    attempt: int,
    base_delay: float = 1.0,
    max_delay: float = 20.0,
    jitter: float = 0.5,
) -> float:
    """Compute exponential backoff with jitter."""
    delay = min(max_delay, base_delay * (2 ** (attempt - 1)))
    return delay + random.uniform(0, jitter)

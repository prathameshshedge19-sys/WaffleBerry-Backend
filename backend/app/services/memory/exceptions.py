"""Safe application exceptions for structured memory extraction."""

from app.services.ai.exceptions import AIServiceError


class MemoryExtractionError(AIServiceError):
    """Base exception for Memory Engine extraction failures."""

    code = "memory_extraction_error"


class MemoryExtractionSourceError(MemoryExtractionError):
    """Raised when supplied source entities do not share one legacy."""

    code = "memory_extraction_source_error"


class MemoryExtractionResponseError(MemoryExtractionError):
    """Raised when provider text cannot become validated candidates."""

    code = "memory_extraction_invalid_response"

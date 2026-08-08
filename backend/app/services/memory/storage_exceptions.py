"""Safe application exceptions for Memory Storage Pipeline boundaries."""


class MemoryStoragePipelineError(Exception):
    """Base error that never carries source text or provider payloads."""


class MemoryOwnershipError(MemoryStoragePipelineError):
    pass


class MemorySourceError(MemoryStoragePipelineError):
    pass


class MemoryPipelineExtractionError(MemoryStoragePipelineError):
    pass


class MemoryPipelineValidationError(MemoryStoragePipelineError):
    pass


class MemoryProvenanceError(MemoryStoragePipelineError):
    pass


class MemoryPipelinePersistenceError(MemoryStoragePipelineError):
    pass


class MemoryCrossLegacyError(MemoryStoragePipelineError):
    pass

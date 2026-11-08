"""Errors the pipeline raises deliberately, so the entry point can report them cleanly."""


class PipelineError(Exception):
    """The pipeline cannot continue."""


class DataValidationError(PipelineError):
    """Data failed a validation check or breached a threshold."""


class SourceUnavailableError(PipelineError):
    """No configured source could supply a required dependency."""

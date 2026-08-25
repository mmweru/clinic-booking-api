class BookingError(Exception):
    """Base class for all booking-domain errors."""


class NotFoundError(BookingError):
    """Raised when a doctor, patient, or appointment doesn't exist."""


class ValidationError(BookingError):
    """Raised for bad input: past times, outside working hours, etc."""


class ConflictError(BookingError):
    """Raised when a slot is already taken (including races caught at the DB level)."""

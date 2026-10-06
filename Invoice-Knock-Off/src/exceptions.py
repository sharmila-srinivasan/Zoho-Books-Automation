"""Custom exceptions.

Every error below means "stop now". None of them is ever caught and ignored on
the way to a financial action: the main program catches them only to save
diagnostics and exit safely.
"""


class ReconciliationError(Exception):
    """Base class for all errors raised by this project."""


class ConfigError(ReconciliationError):
    """The .env configuration or the marketplace rules file is invalid."""


class UserAbortError(ReconciliationError):
    """The user chose to stop (or the input stream ended)."""


class ZohoLoginError(ReconciliationError):
    """Could not confirm a logged-in Zoho Books session."""


class ZohoNavigationError(ReconciliationError):
    """Could not reach the expected Zoho page."""


class ZohoUIChangedError(ReconciliationError):
    """The Zoho page does not look the way the automation expects."""


class PaymentNotFoundError(ReconciliationError):
    """A Payment Received entry could not be found."""


class PaymentVerificationError(ReconciliationError):
    """Payment data read from Zoho does not match what was approved."""


class InvoiceNotFoundError(ReconciliationError):
    """An expected invoice could not be found."""


class InvoiceMismatchError(ReconciliationError):
    """An invoice is in an unexpected or ambiguous state."""


class AmountMismatchError(ReconciliationError):
    """An amount read from Zoho differs from the expected amount."""


class FinancialActionBlockedError(ReconciliationError):
    """A safety rule prevented a financial change (dry run, missing approval, ...)."""

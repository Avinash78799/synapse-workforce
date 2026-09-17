"""Reject accidental raw payment-card material before it reaches storage/models."""
import re

_PAN_LIKE=re.compile(r'(?<![\d-])(?:\d[ -]?){12,18}\d(?![\d-])')


def redact_payment_data(text: str) -> str:
    # Deliberately conservative: token references / order IDs remain supported;
    # long sequences of payment-like digits are not useful agent context.
    return _PAN_LIKE.sub('[payment data removed]',text)

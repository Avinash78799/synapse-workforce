"""Indian PII Detection, Redaction, and Anonymization Engine for DPDP Act, 2023 Compliance.

Detects and sanitizes:
- Aadhaar Numbers (12-digit Indian National Identity)
- Permanent Account Numbers (PAN - 10-character alphanumeric tax ID)
- Indian Mobile Numbers (+91 / 0 followed by 10 digits starting with 6-9)
- Email Addresses
- Payment Card Numbers (12-19 digit PANs)
- Indian Passport Numbers (1 letter + 7 digits)
- Voter ID / EPIC Numbers (3 letters + 7 digits)
"""
import re
import hashlib
from typing import Any

# Aadhaar: 12 digits, formatted as 4-4-4 or continuous 12 digits (not starting with 0 or 1)
_AADHAAR_PATTERN = re.compile(r'\b[2-9]\d{3}[\s-]?\d{4}[\s-]?\d{4}\b')

# Indian PAN: 5 uppercase letters + 4 digits + 1 uppercase letter
_PAN_PATTERN = re.compile(r'\b[A-Z]{5}[0-9]{4}[A-Z]\b', re.IGNORECASE)

# Indian Mobile: +91, 91, or 0 prefix (optional), followed by 10 digits starting with 6, 7, 8, or 9
_PHONE_PATTERN = re.compile(r'(?:\+91[\s-]?)?(?:91[\s-]?)?(?:0[\s-]?)?[6-9]\d{4}[\s-]?\d{5}\b')

# Email Addresses
_EMAIL_PATTERN = re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,7}\b')

# Payment Cards (12-19 digits with optional delimiters)
_CARD_PATTERN = re.compile(r'(?<![\d-])(?:\d[ -]?){12,18}\d(?![\d-])')

# Indian Passport: 1 uppercase letter followed by 7 digits
_PASSPORT_PATTERN = re.compile(r'\b[A-Z][0-9]{7}\b', re.IGNORECASE)

# Voter ID / EPIC: 3 uppercase letters followed by 7 digits
_VOTER_ID_PATTERN = re.compile(r'\b[A-Z]{3}[0-9]{7}\b', re.IGNORECASE)


def redact_pii(text: str) -> str:
    """Sanitizes text by replacing personal identifiers with DPDP-compliant placeholders."""
    if not text:
        return text

    # 1. Payment Cards
    text = _CARD_PATTERN.sub('[PAYMENT_CARD_REDACTED]', text)
    # 2. Aadhaar
    text = _AADHAAR_PATTERN.sub('[AADHAAR_REDACTED]', text)
    # 3. PAN
    text = _PAN_PATTERN.sub('[PAN_REDACTED]', text)
    # 4. Passport
    text = _PASSPORT_PATTERN.sub('[PASSPORT_REDACTED]', text)
    # 5. Voter ID
    text = _VOTER_ID_PATTERN.sub('[VOTER_ID_REDACTED]', text)
    # 6. Email
    text = _EMAIL_PATTERN.sub('[EMAIL_REDACTED]', text)
    # 7. Indian Mobile / Phone
    text = _PHONE_PATTERN.sub('[PHONE_REDACTED]', text)

    return text


def detect_pii(text: str) -> dict[str, int]:
    """Scans text and returns counts of detected PII entities without exposing values."""
    if not text:
        return {}

    counts: dict[str, int] = {}
    
    card_matches = len(_CARD_PATTERN.findall(text))
    if card_matches:
        counts['payment_cards'] = card_matches

    aadhaar_matches = len(_AADHAAR_PATTERN.findall(text))
    if aadhaar_matches:
        counts['aadhaar'] = aadhaar_matches

    pan_matches = len(_PAN_PATTERN.findall(text))
    if pan_matches:
        counts['pan'] = pan_matches

    passport_matches = len(_PASSPORT_PATTERN.findall(text))
    if passport_matches:
        counts['passport'] = passport_matches

    voter_matches = len(_VOTER_ID_PATTERN.findall(text))
    if voter_matches:
        counts['voter_id'] = voter_matches

    email_matches = len(_EMAIL_PATTERN.findall(text))
    if email_matches:
        counts['email'] = email_matches

    phone_matches = len(_PHONE_PATTERN.findall(text))
    if phone_matches:
        counts['phone'] = phone_matches

    return counts


def anonymize_identifier(value: str, salt: str = 'dpdp_salt_v1') -> str:
    """Generates an irreversible, deterministic pseudonym for Data Principal identifiers."""
    digest = hashlib.sha256(f'{salt}:{value.strip().lower()}'.encode('utf-8')).hexdigest()
    return f'ANON_{digest[:16]}'

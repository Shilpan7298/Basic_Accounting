"""GSTIN validation and state resolution.

A GSTIN is 15 characters: 2-digit state code, 10-char PAN, 1 entity number,
'Z', then a checksum character. The checksum is a base-36 weighted sum with
alternating weights 1 and 2, digits of each product summed. Getting this right
matters because the state code is what decides CGST+SGST vs IGST — a typo'd
GSTIN that passes a regex but fails the checksum will silently produce the
wrong tax split on every invoice to that customer.
"""

from __future__ import annotations

import re

GSTIN_RE = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1}$")
_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"

STATE_CODES: dict[str, str] = {
    "01": "Jammu and Kashmir", "02": "Himachal Pradesh", "03": "Punjab",
    "04": "Chandigarh", "05": "Uttarakhand", "06": "Haryana", "07": "Delhi",
    "08": "Rajasthan", "09": "Uttar Pradesh", "10": "Bihar", "11": "Sikkim",
    "12": "Arunachal Pradesh", "13": "Nagaland", "14": "Manipur", "15": "Mizoram",
    "16": "Tripura", "17": "Meghalaya", "18": "Assam", "19": "West Bengal",
    "20": "Jharkhand", "21": "Odisha", "22": "Chhattisgarh", "23": "Madhya Pradesh",
    "24": "Gujarat", "25": "Daman and Diu", "26": "Dadra and Nagar Haveli and Daman and Diu",
    "27": "Maharashtra", "28": "Andhra Pradesh (Old)", "29": "Karnataka", "30": "Goa",
    "31": "Lakshadweep", "32": "Kerala", "33": "Tamil Nadu", "34": "Puducherry",
    "35": "Andaman and Nicobar Islands", "36": "Telangana", "37": "Andhra Pradesh",
    "38": "Ladakh", "96": "Other Country", "97": "Other Territory",
}


class InvalidGSTIN(ValueError):
    pass


def compute_checksum(first_14: str) -> str:
    """Return the expected 15th character for the first 14 of a GSTIN."""
    if len(first_14) != 14:
        raise InvalidGSTIN("checksum needs exactly the first 14 characters")
    total = 0
    for index, char in enumerate(first_14):
        try:
            value = _ALPHABET.index(char)
        except ValueError as exc:
            raise InvalidGSTIN(f"illegal character {char!r} in GSTIN") from exc
        weight = 1 if index % 2 == 0 else 2
        product = value * weight
        # Digit sum in base 36, not base 10.
        total += product // 36 + product % 36
    return _ALPHABET[(36 - total % 36) % 36]


def is_valid(gstin: str | None) -> bool:
    try:
        validate(gstin)
    except InvalidGSTIN:
        return False
    return True


def validate(gstin: str | None) -> str:
    """Return the normalised GSTIN or raise :class:`InvalidGSTIN`."""
    if not gstin:
        raise InvalidGSTIN("GSTIN is empty")
    value = gstin.strip().upper().replace(" ", "")
    if len(value) != 15:
        raise InvalidGSTIN(f"GSTIN must be 15 characters, got {len(value)}")
    if not GSTIN_RE.match(value):
        raise InvalidGSTIN("GSTIN format is invalid")
    if value[:2] not in STATE_CODES:
        raise InvalidGSTIN(f"unknown state code {value[:2]!r}")
    expected = compute_checksum(value[:14])
    if value[14] != expected:
        raise InvalidGSTIN(f"checksum mismatch: expected {expected!r}, got {value[14]!r}")
    return value


def state_code_of(gstin: str | None) -> str | None:
    """State code from a *valid* GSTIN, else ``None``. Never guesses."""
    if not gstin:
        return None
    try:
        return validate(gstin)[:2]
    except InvalidGSTIN:
        return None


def state_name(state_code: str | None) -> str | None:
    if not state_code:
        return None
    return STATE_CODES.get(state_code.zfill(2))

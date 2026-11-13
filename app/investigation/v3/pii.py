"""Mask personal identifiers in free text. Pure functions, no network.

Covers mainland mobile numbers, 18-digit resident ID numbers, bank card numbers that pass
the Luhn check, and e-mail addresses. Names and addresses are not detected.
"""
import re

MOBILE = re.compile(r"(?<!\d)(1[3-9]\d)\d{4}(\d{4})(?!\d)")
RESIDENT_ID = re.compile(r"(?<!\d)(\d{6})(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx](?!\d)")
CARD = re.compile(r"(?<!\d)\d{13,19}(?!\d)")
EMAIL = re.compile(r"([A-Za-z0-9._%+-])[A-Za-z0-9._%+-]*(@[A-Za-z0-9.-]+\.[A-Za-z]{2,})")


def luhn(number):
    total, flip = 0, False
    for char in reversed(number):
        digit = int(char)
        if flip:
            digit = digit * 2 - 9 if digit * 2 > 9 else digit * 2
        total += digit
        flip = not flip
    return total % 10 == 0


def mask(text):
    """Return ``(masked_text, counts)``."""
    counts = dict(mobile=0, resident_id=0, card=0, email=0)

    def replace_id(m):
        counts["resident_id"] += 1
        return m.group(1) + "********" + "****"

    def replace_card(m):
        digits = m.group(0)
        if not luhn(digits):
            return digits
        counts["card"] += 1
        return digits[:4] + "*" * (len(digits) - 8) + digits[-4:]

    def replace_mobile(m):
        counts["mobile"] += 1
        return m.group(1) + "****" + m.group(2)

    def replace_email(m):
        counts["email"] += 1
        return m.group(1) + "***" + m.group(2)

    text = RESIDENT_ID.sub(replace_id, text)
    text = CARD.sub(replace_card, text)
    text = MOBILE.sub(replace_mobile, text)
    text = EMAIL.sub(replace_email, text)
    return text, counts

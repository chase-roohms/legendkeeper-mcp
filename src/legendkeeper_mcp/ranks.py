"""Fractional-index ranks (the ``a0``, ``a1``, ``a0V`` … keys LegendKeeper uses to order siblings).

A port of the ``generateKeyBetween(a, null)`` path of the widely used *fractional-indexing*
algorithm, which is all we need to append after the last sibling.
"""

from __future__ import annotations

DIGITS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
_ZERO = DIGITS[0]


def _integer_length(head: str) -> int:
    if "a" <= head <= "z":
        return ord(head) - ord("a") + 2
    if "A" <= head <= "Z":
        return ord("Z") - ord(head) + 2
    raise ValueError(f"invalid order key head: {head!r}")


def _increment_integer(x: str) -> str | None:
    head, digits = x[0], list(x[1:])
    carry = True
    for i in range(len(digits) - 1, -1, -1):
        d = DIGITS.index(digits[i]) + 1
        if d == len(DIGITS):
            digits[i] = _ZERO
        else:
            digits[i] = DIGITS[d]
            carry = False
            break
    if not carry:
        return head + "".join(digits)
    if head == "Z":
        return "a" + _ZERO
    if head == "z":
        return None
    new_head = chr(ord(head) + 1)
    if new_head > "a":
        digits.append(_ZERO)
    else:
        digits.pop()
    return new_head + "".join(digits)


def _midpoint_after(a: str) -> str:
    """Smallest-ish key strictly greater than fractional part ``a`` (``b`` is unbounded)."""
    digit_a = DIGITS.index(a[0]) if a else 0
    if len(DIGITS) - digit_a > 1:
        return DIGITS[round(0.5 * (digit_a + len(DIGITS)))]
    return DIGITS[digit_a] + _midpoint_after(a[1:])


def rank_after(last: str | None) -> str:
    """Return a rank that sorts after ``last`` (or the first rank when ``last`` is ``None``)."""
    if not last:
        return "a0"
    try:
        length = _integer_length(last[0])
        if len(last) < length:
            raise ValueError("key too short")
        integer, fraction = last[:length], last[length:]
        if any(ch not in DIGITS for ch in last):
            raise ValueError("invalid digit")
        incremented = _increment_integer(integer)
        return integer + _midpoint_after(fraction) if incremented is None else incremented
    except ValueError:
        # Not a standard key: any extension of a string sorts after it.
        return last + "V"

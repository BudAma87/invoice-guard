"""Money in integer minor units. Decimal + ROUND_HALF_UP only; floats never."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

# ISO 4217 exponents for currencies we expect; default 2.
_EXPONENT: dict[str, int] = {
    "LKR": 2,
    "USD": 2,
    "EUR": 2,
    "GBP": 2,
    "INR": 2,
    "AUD": 2,
    "SGD": 2,
    "AED": 2,
    "CNY": 2,
    "JPY": 0,
    "KRW": 0,
    "KWD": 3,
    "BHD": 3,
    "OMR": 3,
}


def exponent(currency: str | None) -> int:
    return _EXPONENT.get((currency or "").upper(), 2)


def to_minor(amount: str | Decimal | int, currency: str | None) -> int:
    """'1,416.50' LKR -> 141650. Raises ValueError on garbage."""
    if isinstance(amount, str):
        cleaned = amount.replace(",", "").replace(" ", "").strip()
        try:
            amount = Decimal(cleaned)
        except InvalidOperation as e:
            raise ValueError(f"not a number: {amount!r}") from e
    d = Decimal(amount) * (Decimal(10) ** exponent(currency))
    return int(d.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def round_minor(value: Decimal) -> int:
    return int(value.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def fmt(minor: int | None, currency: str | None = None, signed: bool = False) -> str:
    """141650, LKR -> '1,416.50'. signed=True -> '+0.50'."""
    if minor is None:
        return "—"
    exp = exponent(currency)
    value = Decimal(minor) / (Decimal(10) ** exp)
    s = f"{value:,.{exp}f}"
    if signed and minor > 0:
        s = "+" + s
    return s


def to_lkr_minor(minor: int, currency: str, fx_to_lkr: Decimal) -> int:
    """Convert minor units of `currency` to LKR minor units at the ERP daily rate."""
    major = Decimal(minor) / (Decimal(10) ** exponent(currency))
    return round_minor(major * fx_to_lkr * (Decimal(10) ** exponent("LKR")))

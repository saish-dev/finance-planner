"""Display filters: Indian-grouped rupees, months, percentages.

Formatting lives here rather than in Django's locale machinery because the
lakh/crore grouping (12,50,000 -- not 1,250,000) and the compact Cr/L forms
on chart axes are specific to how this app is read.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal, InvalidOperation

from django import template
from django.utils.html import escape
from django.utils.safestring import mark_safe

register = template.Library()

RUPEE = "₹"


def _group_indian(digits: str) -> str:
    """1250000 -> 12,50,000. Last three digits, then pairs."""
    if len(digits) <= 3:
        return digits
    head, tail = digits[:-3], digits[-3:]
    parts = []
    while len(head) > 2:
        parts.insert(0, head[-2:])
        head = head[:-2]
    if head:
        parts.insert(0, head)
    return ",".join(parts) + "," + tail


def _plain(amount: Decimal) -> Decimal:
    """Drop trailing zeros without letting Decimal reach for exponent notation.

    `Decimal("50.00").normalize()` is `5E+1`, which would render as "5E+1" on
    screen -- so re-quantize whole numbers back to plain digits.
    """
    trimmed = amount.normalize()
    if trimmed == trimmed.to_integral_value():
        return trimmed.quantize(Decimal("1"))
    return trimmed


def _as_decimal(value) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


@register.filter
def inr(value, places: int = 0):
    """Rupees with Indian grouping, rounded to whole rupees by default."""
    amount = _as_decimal(value)
    if amount is None:
        return "--"
    quant = Decimal(1).scaleb(-int(places))
    amount = amount.quantize(quant)
    sign = "-" if amount < 0 else ""
    text = str(abs(amount))
    whole, _, frac = text.partition(".")
    body = _group_indian(whole) + (f".{frac}" if frac else "")
    return mark_safe(f"{sign}{RUPEE}{body}")


@register.filter
def inr_paise(value):
    """Exact rupees and paise -- used where the arithmetic must be checkable."""
    return inr(value, 2)


@register.filter
def inr_compact(value):
    """Chart-axis form: 1.2 Cr, 12.5 L, 9,500."""
    amount = _as_decimal(value)
    if amount is None:
        return "--"
    sign = "-" if amount < 0 else ""
    magnitude = abs(amount)
    crore = Decimal("10000000")
    lakh = Decimal("100000")
    if magnitude >= crore:
        return mark_safe(f"{sign}{RUPEE}{_plain((magnitude / crore).quantize(Decimal('0.01')))} Cr")
    if magnitude >= lakh:
        return mark_safe(f"{sign}{RUPEE}{_plain((magnitude / lakh).quantize(Decimal('0.1')))} L")
    return inr(amount)


@register.filter
def month_label(value):
    """1 Jan 2026 -> 'Jan 2026'."""
    if not isinstance(value, dt.date):
        return value or "--"
    return value.strftime("%b %Y")


@register.filter
def pct(value):
    """12.00 -> '12%', 7.50 -> '7.5%'."""
    amount = _as_decimal(value)
    if amount is None:
        return "--"
    # Derived rates (the value-weighted blended return) carry full Decimal
    # precision, so round before showing rather than printing 25 digits.
    return f"{_plain(amount.quantize(Decimal('0.01')))}%"


@register.filter
def net_class(value):
    """CSS hook so negative money reads as negative at a glance."""
    amount = _as_decimal(value)
    if amount is None:
        return ""
    if amount < 0:
        return "negative"
    if amount > 0:
        return "positive"
    return "zero"


@register.simple_tag
def info(text):
    """A small 'i' that explains how the number beside it is worked out.

    Plain HTML and CSS: the explanation lives in a data attribute and is shown
    on hover *and* on keyboard focus, so it is not mouse-only. Also mirrored
    into aria-label for screen readers.
    """
    safe = escape(text)
    return mark_safe(
        f'<span class="info" tabindex="0" role="note" aria-label="{safe}" data-info="{safe}">i</span>'
    )


@register.simple_tag
def query_with(request, **kwargs):
    """Rebuild the query string with some parameters replaced."""
    params = request.GET.copy()
    for key, value in kwargs.items():
        if value is None or value == "":
            params.pop(key, None)
        else:
            params[key] = value
    encoded = params.urlencode()
    return f"?{encoded}" if encoded else ""

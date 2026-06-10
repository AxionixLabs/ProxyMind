# -*- coding: utf-8 -*-

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Iterable, Sequence, TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class CartItem:
    name: str
    price: Decimal
    quantity: int


def celsius_to_fahrenheit(value: float) -> float:
    return value * 9 / 5 + 32


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return re.sub(r"-+", "-", slug)


def mask_email(value: str) -> str:
    if "@" not in value:
        raise ValueError("email must contain @")
    local, domain = value.split("@", 1)
    if not local or not domain:
        raise ValueError("email must contain local and domain parts")
    if len(local) == 1:
        return f"*@{domain}"
    return f"{local[0]}***{local[-1]}@{domain}"


def chunked(items: Sequence[T], size: int) -> list[list[T]]:
    if size <= 0:
        raise ValueError("size must be positive")
    return [list(items[index:index + size]) for index in range(0, len(items), size)]


def flatten(groups: Iterable[Iterable[T]]) -> list[T]:
    return [item for group in groups for item in group]


def merge_settings(defaults: dict[str, object], overrides: dict[str, object]) -> dict[str, object]:
    return {**defaults, **overrides}


def grade_summary(scores: Sequence[int]) -> dict[str, int]:
    if not scores:
        raise ValueError("scores must not be empty")
    return {
        "min": min(scores),
        "max": max(scores),
        "average": round(sum(scores) / len(scores))
    }


def days_until(target: date, *, today: date | None = None) -> int:
    baseline = today or date.today()
    return (target - baseline).days


def safe_filename(path: str) -> str:
    name = Path(path).name
    if not name:
        raise ValueError("path must contain a filename")
    return re.sub(r"[^A-Za-z0-9._-]", "_", name)


def cart_total(items: Iterable[CartItem], *, tax_rate: Decimal = Decimal("0")) -> Decimal:
    subtotal = sum((item.price * item.quantity for item in items), Decimal("0"))
    total = subtotal * (Decimal("1") + tax_rate)
    return total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def title_case_words(value: str) -> str:
    return " ".join(word.capitalize() for word in value.split())

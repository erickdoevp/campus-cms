"""
Sales figures for the accountant's dashboard.

Definitions (all in MXN, by local day in settings.TIME_ZONE):
- gross: money that came in during the period (orders by ``paid_at``), including
  orders refunded later;
- refunds: money that went back during the period (orders by ``refunded_at``);
- net: gross - refunds.

Days are bucketed in Python rather than with SQL date functions: those need
MySQL's time zone tables, which a stock install doesn't load. Volumes are
small (one row per sale), so this stays cheap.
"""
from collections import defaultdict
from datetime import datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db.models import Count, Q

from catalog.models import Product

from .models import Order

ZERO = Decimal("0.00")
# Up to this many days the chart has one column per day, then one per week.
DAILY_UP_TO = 62


def _money(value):
    """Amounts leave as "1234.50" strings, like the rest of the API: never floats."""
    if isinstance(value, Decimal):
        return str(value.quantize(Decimal("0.01")))
    if isinstance(value, dict):
        return {key: _money(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_money(item) for item in value]
    return value


def _bounds(start, end):
    """[start 00:00, end+1 00:00) in local time, as aware datetimes."""
    zone = ZoneInfo(settings.TIME_ZONE)
    return datetime.combine(start, time.min, zone), datetime.combine(end + timedelta(days=1), time.min, zone)


def _bucket(moment, weekly):
    day = moment.astimezone(ZoneInfo(settings.TIME_ZONE)).date()
    return day - timedelta(days=day.weekday()) if weekly else day


def _totals(since, until):
    paid = list(Order.objects.filter(paid_at__gte=since, paid_at__lt=until).values_list("amount", "user_id"))
    refunds = list(Order.objects.filter(refunded_at__gte=since, refunded_at__lt=until).values_list("amount", flat=True))
    gross = sum((amount for amount, _ in paid), ZERO)
    refunded = sum(refunds, ZERO)
    return {
        "gross": gross,
        "refunds": refunded,
        "net": gross - refunded,
        "paid_orders": len(paid),
        "refunded_orders": len(refunds),
        "buyers": len({user for _, user in paid}),
        "average_order": (gross / len(paid)).quantize(Decimal("0.01")) if paid else None,
    }


def dashboard(start, end):
    """Everything the dashboard shows for the days start..end (inclusive)."""
    since, until = _bounds(start, end)
    days = (end - start).days + 1
    previous = _totals(*_bounds(start - timedelta(days=days), start - timedelta(days=1)))

    # Time series, with an entry for every bucket (also empty ones).
    weekly = days > DAILY_UP_TO
    first = start - timedelta(days=start.weekday()) if weekly else start
    step = timedelta(days=7 if weekly else 1)
    series = {}
    bucket = first
    while bucket <= end:
        series[bucket] = {"gross": ZERO, "refunds": ZERO, "paid_orders": 0}
        bucket += step
    by_product = defaultdict(lambda: {"sold": 0, "gross": ZERO, "refunds": ZERO, "refunded": 0})
    for paid_at, amount, product_id in Order.objects.filter(paid_at__gte=since, paid_at__lt=until).values_list(
        "paid_at", "amount", "product_id"
    ):
        point = series[_bucket(paid_at, weekly)]
        point["gross"] += amount
        point["paid_orders"] += 1
        by_product[product_id]["sold"] += 1
        by_product[product_id]["gross"] += amount
    for refunded_at, amount, product_id in Order.objects.filter(
        refunded_at__gte=since, refunded_at__lt=until
    ).values_list("refunded_at", "amount", "product_id"):
        series[_bucket(refunded_at, weekly)]["refunds"] += amount
        by_product[product_id]["refunds"] += amount
        by_product[product_id]["refunded"] += 1

    # Every product with movement in the period, plus the ones on sale (to show zeros).
    products = Product.objects.filter(Q(pk__in=by_product.keys()) | Q(status=Product.Status.ON_SALE))
    courses = sorted(
        (
            {
                "product_id": product.pk,
                "title": product.title,
                "course_key": product.course_key,
                "status": product.status,
                **by_product[product.pk],
                "net": by_product[product.pk]["gross"] - by_product[product.pk]["refunds"],
            }
            for product in products
        ),
        key=lambda row: (-row["net"], row["title"]),
    )

    # Current state, not tied to the period: what needs attention now.
    attention = Order.objects.aggregate(
        access_errors=Count("pk", filter=Q(access=Order.Access.ERROR)),
        pending=Count("pk", filter=Q(status=Order.Status.PENDING)),
    )

    return _money({
        "from": start,
        "to": end,
        "granularity": "week" if weekly else "day",
        "totals": _totals(since, until),
        "previous": previous,
        "series": [
            {"date": day, **point, "net": point["gross"] - point["refunds"]} for day, point in series.items()
        ],
        "courses": courses,
        "attention": attention,
    })

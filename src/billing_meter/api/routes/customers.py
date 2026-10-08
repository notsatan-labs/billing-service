from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, Request

from billing_meter.api.context import AppContext, context
from billing_meter.api.schemas import (
    CostLineModel,
    InvoiceResponse,
    SummaryResponse,
    UsageLine,
    UsageResponse,
    WindowInfo,
)
from billing_meter.domain.errors import problem, validation_error
from billing_meter.domain.events import validate_id
from billing_meter.domain.pricing import (
    CURRENCY,
    Costing,
    format_cents,
    format_price,
    price_usage,
)
from billing_meter.domain.timestamps import format_timestamp
from billing_meter.domain.windows import Window, resolve_window
from billing_meter.services.invoices import build_invoice, parse_period
from billing_meter.services.usage import aggregate_usage, format_quantity

router = APIRouter(prefix="/v1/customers/{customer_id}")

WindowParam = Annotated[str | None, Query()]
FromParam = Annotated[str | None, Query(alias="from")]
ToParam = Annotated[str | None, Query(alias="to")]


@router.get("/usage", response_model=UsageResponse)
def get_usage(
    request: Request,
    customer_id: str,
    window: WindowParam = None,
    start: FromParam = None,
    end: ToParam = None,
) -> UsageResponse:
    customer_id, resolved, totals = window_totals(
        context(request), customer_id, window, start, end
    )
    return UsageResponse(
        customer_id=customer_id,
        window=window_info(resolved),
        usage=[
            UsageLine(resource_type=resource, quantity=format_quantity(micros))
            for resource, micros in totals.items()
        ],
    )


@router.get("/summary", response_model=SummaryResponse)
def get_summary(
    request: Request,
    customer_id: str,
    window: WindowParam = None,
    start: FromParam = None,
    end: ToParam = None,
) -> SummaryResponse:
    customer_id, resolved, totals = window_totals(
        context(request), customer_id, window, start, end
    )
    costing = price_usage(totals)
    return SummaryResponse(
        customer_id=customer_id,
        window=window_info(resolved),
        currency=CURRENCY,
        lines=cost_lines(costing),
        pricing_table=costing.pricing_table,
        total=format_cents(costing.total_cents),
    )


@router.get(
    "/invoices/{period}",
    response_model=InvoiceResponse,
    responses={404: {"description": "Month not closed yet; see Retry-After"}},
)
def get_invoice(request: Request, customer_id: str, period: str) -> InvoiceResponse:
    ctx = context(request)
    customer_id = require_customer_id(customer_id)
    year, month = parse_period(period)
    with ctx.db.connect() as conn:
        invoice = build_invoice(
            conn,
            customer_id,
            year,
            month,
            now_fn=ctx.clock.now,
            close_grace_seconds=ctx.settings.close_grace_seconds,
        )
    return InvoiceResponse(
        invoice_id=invoice.invoice_id,
        customer_id=invoice.customer_id,
        period=invoice.period,
        period_start=format_timestamp(invoice.start),
        period_end=format_timestamp(invoice.end),
        closed_at=format_timestamp(invoice.closed_at),
        currency=CURRENCY,
        lines=cost_lines(invoice.costing),
        pricing_table=invoice.costing.pricing_table,
        total=format_cents(invoice.costing.total_cents),
    )


def require_customer_id(raw: str) -> str:
    customer_id, error = validate_id(raw)
    if error is not None:
        raise validation_error([problem(error, field="customer_id")])
    assert customer_id is not None
    return customer_id


def window_totals(
    ctx: AppContext,
    customer_id: str,
    window: str | None,
    start: str | None,
    end: str | None,
) -> tuple[str, Window, dict[str, int]]:
    customer_id = require_customer_id(customer_id)
    resolved = resolve_window(window, start, end, now=ctx.clock.now())
    with ctx.db.connect() as conn:
        totals = aggregate_usage(conn, customer_id, resolved.start, resolved.end)
    return customer_id, resolved, totals


def window_info(window: Window) -> WindowInfo:
    return WindowInfo(
        name=window.name,
        start=format_timestamp(window.start),
        end=format_timestamp(window.end),
    )


def cost_lines(costing: Costing) -> list[CostLineModel]:
    return [
        CostLineModel(
            resource_type=line.resource_type,
            quantity=format_quantity(line.quantity_micros),
            unit_price=format_price(line.price_ticks),
            cost=format_cents(line.cost_cents),
        )
        for line in costing.lines
    ]

"""Show the curves: a Markdown table for the options flow, and SVG charts.

Pure functions only, so they are easy to test. No plotting library is needed.
"""

from __future__ import annotations

from dataclasses import dataclass
from xml.sax.saxutils import escape

from .return_curve import Point, clamp, interpolate

# Colours that read on both light and dark dashboards.
_TEXT = "#8a8a8a"
_GRID = "#8a8a8a"
_CURVE = "#03a9f4"
_CLAMP = "#e53935"
_TARGET = "#ff9800"
_CURRENT = "#43a047"


def _fmt(value: float) -> str:
    return f"{value:g}"


def preview_rows(
    points: list[Point], min_value: float, max_value: float
) -> list[tuple[float, float, bool]]:
    """Return (outdoor, limit, clamped) for each point and 4 K beyond each end."""
    xs = sorted({points[0][0] - 4, *(x for x, _ in points), points[-1][0] + 4})
    rows = []
    for x in xs:
        raw = interpolate(points, x)
        limit = round(clamp(raw, min_value, max_value), 1)
        rows.append((x, limit, abs(limit - raw) > 1e-9))
    return rows


def preview_table(points: list[Point], min_value: float, max_value: float) -> str:
    """A Markdown table of the curve, warmest first, as the options flow shows it."""
    lines = ["| Outdoor | Return limit |", "|---:|---:|"]
    any_clamped = False
    for x, limit, clamped in reversed(preview_rows(points, min_value, max_value)):
        any_clamped |= clamped
        mark = " *" if clamped else ""
        lines.append(f"| {_fmt(x)} °C | {_fmt(limit)} °C{mark} |")
    if any_clamped:
        lines.append("")
        lines.append(
            f"\\* cut to the {_fmt(min_value)}–{_fmt(max_value)} °C range"
        )
    return "\n".join(lines)


@dataclass(frozen=True, slots=True)
class Marker:
    """A point drawn at the current outdoor temperature, with a legend line."""

    value: float
    label: str
    colour: str
    filled: bool = True


def _line_at(points: list[Point], x: float, extrapolate: bool) -> float:
    """The curve at x: flat beyond its ends, or continuing its end slopes."""
    if extrapolate and len(points) >= 2:
        if x < points[0][0]:
            (x0, y0), (x1, y1) = points[0], points[1]
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
        if x > points[-1][0]:
            (x0, y0), (x1, y1) = points[-2], points[-1]
            return y1 + (y1 - y0) * (x - x1) / (x1 - x0)
    return interpolate(points, x)


def render_chart(
    points: list[Point],
    min_value: float,
    max_value: float,
    *,
    y_label: str,
    outdoor: float | None = None,
    markers: list[Marker] | None = None,
    status: str | None = None,
    extrapolate: bool = False,
    show_limits: bool = False,
    width: int = 640,
    height: int = 360,
) -> str:
    """Draw a curve cut to min/max, and markers at the current outdoor temperature.

    extrapolate continues the end slopes instead of staying flat (the ECL heat curve).
    show_limits keeps min and max inside the chart even where the curve doesn't reach.
    """
    markers = markers or []
    left, right, top, bottom = 48, 16, 34, 40
    plot_w = width - left - right
    plot_h = height - top - bottom

    # Outdoor axis: the curve plus 5 K either side, and the current outdoor value.
    x_values = [x for x, _ in points] + ([outdoor] if outdoor is not None else [])
    x_lo = _floor5(min(x_values) - 5)
    x_hi = _ceil5(max(x_values) + 5)

    def limit_at(x: float) -> float:
        return clamp(_line_at(points, x, extrapolate), min_value, max_value)

    # Where the line bends: its points, the chart edges, and where it meets min/max.
    breaks = sorted({x_lo, x_hi, *(x for x, _ in points)})
    curve_xs = set(breaks)
    for a, b in zip(breaks, breaks[1:]):
        ya, yb = _line_at(points, a, extrapolate), _line_at(points, b, extrapolate)
        for bound in (min_value, max_value):
            if (ya - bound) * (yb - bound) < 0:
                curve_xs.add(a + (bound - ya) * (b - a) / (yb - ya))
    curve_xs = sorted(curve_xs)

    y_values = [limit_at(x) for x in curve_xs] + [m.value for m in markers]
    if show_limits:
        y_values += [min_value, max_value]
    y_lo = _floor5(min(y_values) - 3)
    y_hi = _ceil5(max(y_values) + 3)

    def px(x: float) -> float:
        return left + (x - x_lo) / (x_hi - x_lo) * plot_w

    def py(y: float) -> float:
        return top + (y_hi - y) / (y_hi - y_lo) * plot_h

    out: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" font-family="sans-serif" font-size="12">'
    ]

    # Grid and axis labels: every 5 degrees outdoor; every 5 or 10 up the side.
    for x in range(int(x_lo), int(x_hi) + 1, 5):
        out.append(
            f'<line x1="{px(x):.1f}" y1="{top}" x2="{px(x):.1f}" y2="{top + plot_h}" '
            f'stroke="{_GRID}" stroke-opacity="0.25"/>'
        )
        out.append(
            f'<text x="{px(x):.1f}" y="{top + plot_h + 16}" fill="{_TEXT}" '
            f'text-anchor="middle">{x}</text>'
        )
    y_step = 5 if y_hi - y_lo <= 50 else 10
    for y in range(int(-y_step * (-y_lo // y_step)), int(y_hi) + 1, y_step):
        out.append(
            f'<line x1="{left}" y1="{py(y):.1f}" x2="{left + plot_w}" y2="{py(y):.1f}" '
            f'stroke="{_GRID}" stroke-opacity="0.25"/>'
        )
        out.append(
            f'<text x="{left - 6}" y="{py(y) + 4:.1f}" fill="{_TEXT}" '
            f'text-anchor="end">{y}</text>'
        )
    out.append(
        f'<text x="{left + plot_w / 2:.1f}" y="{height - 6}" fill="{_TEXT}" '
        'text-anchor="middle">Outdoor °C</text>'
    )
    out.append(
        f'<text x="{left - 40}" y="{top - 14}" fill="{_TEXT}">{escape(y_label)}</text>'
    )

    # Min/max limits, where they fall inside the chart.
    for bound in (min_value, max_value):
        if y_lo <= bound <= y_hi:
            out.append(
                f'<line x1="{left}" y1="{py(bound):.1f}" x2="{left + plot_w}" '
                f'y2="{py(bound):.1f}" stroke="{_CLAMP}" stroke-width="1" '
                'stroke-dasharray="6 4" stroke-opacity="0.7"/>'
            )

    # The curve and its points.
    path = " ".join(f"{px(x):.1f},{py(limit_at(x)):.1f}" for x in curve_xs)
    out.append(
        f'<polyline points="{path}" fill="none" stroke="{_CURVE}" stroke-width="2.5" '
        'stroke-linejoin="round"/>'
    )
    for x, _ in points:
        out.append(
            f'<circle cx="{px(x):.1f}" cy="{py(limit_at(x)):.1f}" r="3.5" fill="{_CURVE}"/>'
        )

    # Where things are now.
    if outdoor is not None and markers:
        out.append(
            f'<line x1="{px(outdoor):.1f}" y1="{top}" x2="{px(outdoor):.1f}" '
            f'y2="{top + plot_h}" stroke="{_TARGET}" stroke-dasharray="3 3"/>'
        )
        for marker in markers:
            if marker.filled:
                style = f'fill="{marker.colour}"'
            else:
                style = f'fill="none" stroke="{marker.colour}" stroke-width="2.5"'
            out.append(
                f'<circle cx="{px(outdoor):.1f}" cy="{py(marker.value):.1f}" r="6" {style}/>'
            )

    # Legend, top right of the plot (a falling curve leaves it empty), on a backing
    # box so the lines behind it don't run through the text.
    legend = [(m.colour, m.label) for m in markers] if outdoor is not None else []
    if status:
        legend.append((_TEXT, f"Status: {status}"))
    if legend:
        box_w = 7 * max(len(text) for _, text in legend) + 12
        box_h = 16 * len(legend) + 8
        out.append(
            f'<rect x="{left + plot_w - box_w:.1f}" y="{top + 2}" width="{box_w}" '
            f'height="{box_h}" rx="4" fill="#808080" fill-opacity="0.2"/>'
        )
    for i, (colour, text) in enumerate(legend):
        out.append(
            f'<text x="{left + plot_w - 6}" y="{top + 16 + 16 * i}" fill="{colour}" '
            f'text-anchor="end">{escape(text)}</text>'
        )

    out.append("</svg>")
    return "\n".join(out)


def render_svg(
    points: list[Point],
    min_value: float,
    max_value: float,
    *,
    outdoor: float | None = None,
    target: float | None = None,
    current: float | None = None,
    status: str | None = None,
) -> str:
    """The return limit curve: target at the averaged outdoor temperature, and 21210."""
    markers = []
    if outdoor is not None and target is not None:
        markers.append(
            Marker(target, f"Target {_fmt(target)} °C at {outdoor:.1f} °C outdoor", _TARGET)
        )
        if current is not None:
            markers.append(
                Marker(current, f"ECL holds {current:.1f} °C", _CURRENT, filled=False)
            )
    return render_chart(
        points,
        min_value,
        max_value,
        y_label="Return limit °C",
        outdoor=outdoor if markers else None,
        markers=markers,
        status=status,
    )


def render_supply_svg(
    points: list[Point],
    min_value: float,
    max_value: float,
    *,
    outdoor: float | None = None,
    curve_reference: float | None = None,
    flow_reference: float | None = None,
    flow: float | None = None,
) -> str:
    """The ECL heat curve: continued beyond its points and cut to the supply min/max.

    Markers at the outdoor temperature: what the curve asks for (weather-compensated
    reference), the reference after limiters, and the actual flow temperature.
    """
    markers = []
    if outdoor is not None:
        if curve_reference is not None:
            markers.append(
                Marker(
                    curve_reference,
                    f"Curve {curve_reference:.1f} °C at {outdoor:.1f} °C outdoor",
                    _TARGET,
                )
            )
        if flow_reference is not None:
            markers.append(
                Marker(flow_reference, f"Reference {flow_reference:.1f} °C", _CLAMP, False)
            )
        if flow is not None:
            markers.append(Marker(flow, f"Flow {flow:.1f} °C", _CURRENT, False))
    return render_chart(
        points,
        min_value,
        max_value,
        y_label="Supply °C",
        outdoor=outdoor if markers else None,
        markers=markers,
        extrapolate=True,
        show_limits=True,
    )


def _floor5(value: float) -> float:
    return 5 * (value // 5)


def _ceil5(value: float) -> float:
    return -5 * (-value // 5)

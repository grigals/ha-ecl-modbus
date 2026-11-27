"""Show the return limit curve: a Markdown table for the options flow, an SVG chart.

Pure functions only, so they are easy to test. No plotting library is needed.
"""

from __future__ import annotations

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


def render_svg(
    points: list[Point],
    min_value: float,
    max_value: float,
    *,
    outdoor: float | None = None,
    target: float | None = None,
    current: float | None = None,
    status: str | None = None,
    width: int = 640,
    height: int = 360,
) -> str:
    """Draw the curve, the min/max limits and the current operating point."""
    left, right, top, bottom = 48, 16, 34, 40
    plot_w = width - left - right
    plot_h = height - top - bottom

    # Outdoor axis: the curve plus 5 K either side, and the current outdoor value.
    x_values = [x for x, _ in points] + ([outdoor] if outdoor is not None else [])
    x_lo = _floor5(min(x_values) - 5)
    x_hi = _ceil5(max(x_values) + 5)

    def limit_at(x: float) -> float:
        return clamp(interpolate(points, x), min_value, max_value)

    curve_xs = sorted({x_lo, x_hi, *(x for x, _ in points)})
    y_values = [limit_at(x) for x in curve_xs]
    y_values += [v for v in (target, current) if v is not None]
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

    # Grid and axis labels every 5 degrees.
    for x in range(int(x_lo), int(x_hi) + 1, 5):
        out.append(
            f'<line x1="{px(x):.1f}" y1="{top}" x2="{px(x):.1f}" y2="{top + plot_h}" '
            f'stroke="{_GRID}" stroke-opacity="0.25"/>'
        )
        out.append(
            f'<text x="{px(x):.1f}" y="{top + plot_h + 16}" fill="{_TEXT}" '
            f'text-anchor="middle">{x}</text>'
        )
    for y in range(int(y_lo), int(y_hi) + 1, 5):
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
    out.append(f'<text x="{left - 40}" y="{top - 14}" fill="{_TEXT}">Return limit °C</text>')

    # Min/max limits, where they fall inside the chart.
    for bound in (min_value, max_value):
        if y_lo <= bound <= y_hi:
            out.append(
                f'<line x1="{left}" y1="{py(bound):.1f}" x2="{left + plot_w}" '
                f'y2="{py(bound):.1f}" stroke="{_CLAMP}" stroke-width="1" '
                'stroke-dasharray="6 4" stroke-opacity="0.7"/>'
            )

    # The curve, flat beyond its ends and cut to min/max, and its points.
    path = " ".join(f"{px(x):.1f},{py(limit_at(x)):.1f}" for x in curve_xs)
    out.append(
        f'<polyline points="{path}" fill="none" stroke="{_CURVE}" stroke-width="2.5" '
        'stroke-linejoin="round"/>'
    )
    for x, _ in points:
        out.append(
            f'<circle cx="{px(x):.1f}" cy="{py(limit_at(x)):.1f}" r="3.5" fill="{_CURVE}"/>'
        )

    # Where the curve is now, and what the controller holds.
    legend: list[tuple[str, str]] = []
    if outdoor is not None and target is not None:
        out.append(
            f'<line x1="{px(outdoor):.1f}" y1="{top}" x2="{px(outdoor):.1f}" '
            f'y2="{top + plot_h}" stroke="{_TARGET}" stroke-dasharray="3 3"/>'
        )
        out.append(
            f'<circle cx="{px(outdoor):.1f}" cy="{py(target):.1f}" r="6" fill="{_TARGET}"/>'
        )
        legend.append((_TARGET, f"Target {_fmt(target)} °C at {outdoor:.1f} °C outdoor"))
    if outdoor is not None and current is not None:
        out.append(
            f'<circle cx="{px(outdoor):.1f}" cy="{py(current):.1f}" r="6" fill="none" '
            f'stroke="{_CURRENT}" stroke-width="2.5"/>'
        )
        legend.append((_CURRENT, f"ECL holds {current:.1f} °C"))
    if status:
        legend.append((_TEXT, f"Status: {status}"))

    # Top right of the plot: a falling curve leaves it empty.
    for i, (colour, text) in enumerate(legend):
        out.append(
            f'<text x="{left + plot_w - 6}" y="{top + 16 + 16 * i}" fill="{colour}" '
            f'text-anchor="end">{escape(text)}</text>'
        )

    out.append("</svg>")
    return "\n".join(out)


def _floor5(value: float) -> float:
    return 5 * (value // 5)


def _ceil5(value: float) -> float:
    return -5 * (-value // 5)

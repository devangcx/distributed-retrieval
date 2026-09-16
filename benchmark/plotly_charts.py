"""Render benchmark chart data as static PNG files with Plotly."""

from pathlib import Path

import plotly.graph_objects as go


ChartValue = tuple[str, float, float | None, float | None]


def horizontal_bar_figure(
    title: str,
    value_label: str,
    values: list[ChartValue],
    show_zero_line: bool = False,
    show_values: bool = True,
) -> go.Figure:
    """Build a horizontal bar figure with optional confidence intervals."""
    labels = [label for label, _value, _lower, _upper in values]
    means = [value for _label, value, _lower, _upper in values]
    upper_errors = [
        0.0 if upper is None else max(0.0, upper - value)
        for _label, value, _lower, upper in values
    ]
    lower_errors = [
        0.0 if lower is None else max(0.0, value - lower)
        for _label, value, lower, _upper in values
    ]
    has_intervals = any(
        lower is not None and upper is not None
        for _label, _value, lower, upper in values
    )
    plotted_bounds = [0.0]
    for _label, value, lower, upper in values:
        plotted_bounds.append(value)
        plotted_bounds.append(value if lower is None else lower)
        plotted_bounds.append(value if upper is None else upper)
    minimum = min(plotted_bounds)
    maximum = max(plotted_bounds)
    value_span = maximum - minimum
    if value_span == 0.0:
        value_span = 1.0
    annotation_gap = value_span * 0.015
    range_padding = value_span * 0.12

    figure = go.Figure(
        go.Bar(
            x=means,
            y=labels,
            orientation="h",
            marker_color="#3975a8",
            error_x={
                "type": "data",
                "symmetric": False,
                "array": upper_errors,
                "arrayminus": lower_errors,
                "visible": has_intervals,
            },
            cliponaxis=False,
        )
    )
    figure.update_layout(
        title={"text": title, "x": 0.5},
        template="plotly_white",
        width=1600,
        height=max(500, 150 + 34 * len(values)),
        margin={"l": 480, "r": 140, "t": 90, "b": 80},
        xaxis_title=value_label,
        yaxis={"autorange": "reversed", "automargin": True},
        showlegend=False,
    )
    figure.update_xaxes(
        range=[minimum - range_padding, maximum + range_padding]
    )
    if show_values:
        for label, value, lower, upper in values:
            if value < 0.0:
                interval_edge = value if lower is None else lower
                annotation_x = interval_edge - annotation_gap
                anchor = "right"
            else:
                interval_edge = value if upper is None else upper
                annotation_x = interval_edge + annotation_gap
                anchor = "left"
            figure.add_annotation(
                x=annotation_x,
                y=label,
                text=f"{value:.3f}",
                showarrow=False,
                xanchor=anchor,
                yanchor="middle",
                font={"size": 11, "color": "#333333"},
            )
    if show_zero_line:
        figure.add_vline(x=0.0, line_width=1, line_color="#555555")
    return figure


def write_horizontal_bar_png(
    path: Path,
    title: str,
    value_label: str,
    values: list[ChartValue],
    overwrite: bool = False,
    show_zero_line: bool = False,
    show_values: bool = True,
) -> None:
    """Render one benchmark chart through the static Plotly interface."""
    if path.exists() and not overwrite:
        raise FileExistsError(f"report artifact already exists: {path}")
    figure = horizontal_bar_figure(
        title,
        value_label,
        values,
        show_zero_line=show_zero_line,
        show_values=show_values,
    )
    figure.write_image(path, format="png", scale=2)

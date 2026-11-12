"""Pure helpers and the sparkline drawing of the portfolio cards, with no display needed.

The sparkline is drawn onto a recording stand-in for a Tk canvas, so the drawing
logic runs without a window.
"""

from __future__ import annotations

import pytest

from reports import portfolio_cards as pc


class RecordingCanvas:
    def __init__(self):
        self.calls: list[tuple[str, tuple, dict]] = []

    def delete(self, *args):
        self.calls.append(("delete", args, {}))

    def configure(self, **kwargs):
        self.calls.append(("configure", (), kwargs))

    def create_line(self, *args, **kwargs):
        self.calls.append(("line", args, kwargs))

    def create_polygon(self, *args, **kwargs):
        self.calls.append(("polygon", args, kwargs))

    def kinds(self):
        return [c[0] for c in self.calls]


def test_spark_pct_and_zero_baseline():
    assert pc._spark_pct(110.0, 100.0) == pytest.approx(10.0)
    assert pc._spark_pct(90.0, 100.0) == pytest.approx(-10.0)
    assert pc._spark_pct(50.0, 0.0) == 0.0


def test_spark_y_maps_top_centre_and_bottom():
    top = pc._spark_y(pc.SPARK_SCALE_PCT, 20)
    mid = pc._spark_y(0.0, 20)
    bottom = pc._spark_y(-pc.SPARK_SCALE_PCT, 20)
    assert top < mid < bottom
    # Values beyond the scale are clamped to the edges.
    assert pc._spark_y(999.0, 20) == pytest.approx(top)


def test_sparkline_with_too_few_points_draws_a_flat_line():
    canvas = RecordingCanvas()
    pc.draw_sparkline(canvas, [100.0], width=60, height=20)
    assert canvas.kinds() == ["delete", "configure", "line"]


def test_sparkline_with_zero_baseline_draws_a_flat_line():
    canvas = RecordingCanvas()
    pc.draw_sparkline(canvas, [1.0, 2.0, 3.0], baseline=0.0)
    assert canvas.kinds()[-1] == "line"
    assert "polygon" not in canvas.kinds()


def test_sparkline_draws_fill_for_each_segment():
    canvas = RecordingCanvas()
    pc.draw_sparkline(canvas, [100.0, 101.0, 100.5, 99.0], width=80, height=20)
    assert canvas.kinds().count("polygon") == 3
    assert canvas.kinds().count("line") >= 4  # baseline plus one line per segment


def test_sparkline_colours_rising_and_falling_segments_differently():
    rising = RecordingCanvas()
    pc.draw_sparkline(rising, [100.0, 102.0])
    falling = RecordingCanvas()
    pc.draw_sparkline(falling, [100.0, 98.0])
    rising_fill = [c[2]["fill"] for c in rising.calls if c[0] == "polygon"][0]
    falling_fill = [c[2]["fill"] for c in falling.calls if c[0] == "polygon"][0]
    assert rising_fill != falling_fill


def test_sparkline_custom_scale_is_used():
    canvas = RecordingCanvas()
    pc.draw_sparkline(canvas, [100.0, 100.5], scale_pct=0.5)
    assert canvas.kinds()[0] == "delete"


@pytest.mark.parametrize(
    "amount, loc, expected",
    [
        (None, "", "—"),
        (1234.5, "", "$1,234.50"),
        (1234.5, "AUS", "A$1,234.50"),
        (1234.5, "aus", "A$1,234.50"),
    ],
)
def test_format_price(amount, loc, expected):
    assert pc._fmt_price(amount, loc) == expected


@pytest.mark.parametrize(
    "amount, loc, expected",
    [
        (None, "", "—"),
        (12.0, "", "+$12.00"),
        (-12.0, "", "$-12.00"),
        (0.0, "AUS", "A$0.00"),
        (5.0, "AUS", "+A$5.00"),
    ],
)
def test_format_change(amount, loc, expected):
    assert pc._fmt_change(amount, loc) == expected


def test_format_change_pct():
    assert pc._fmt_change_pct(None) == "—"
    assert pc._fmt_change_pct(2.5) == "+2.50%"
    assert pc._fmt_change_pct(-1.25) == "-1.25%"


def test_profit_and_loss_colours():
    assert pc._pl_color(None) == pc._FG
    assert pc._pl_color(0.0) == pc._GOOD
    assert pc._pl_color(10.0) == pc._GOOD
    assert pc._pl_color(-1.0) == pc._BAD

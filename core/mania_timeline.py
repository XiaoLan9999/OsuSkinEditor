"""Preview-only Mania timing: a fixed rhythm and independently adjustable speed.

The scroll-time reference is MAX_TIME_RANGE / scrollSpeed in osu!lazer's
DrawableManiaRuleset. Scaling by HitPosition preserves travel velocity when
the target moves. This demo does not read beatmaps or apply gameplay mods.
https://github.com/ppy/osu/blob/master/osu.Game.Rulesets.Mania/UI/DrawableManiaRuleset.cs
"""
import math


MAX_TIME_RANGE = 11485.0
DEFAULT_HIT_POSITION = 402.0


def bounded_number(value, minimum, maximum, default):
    try:
        number = float(value)
        return max(minimum, min(maximum, number)) if math.isfinite(number) else default
    except (TypeError, ValueError):
        return default


def travel_time_ms(scroll_speed=20, hit_position=DEFAULT_HIT_POSITION):
    speed = bounded_number(scroll_speed, 1, 40, 20)
    position = bounded_number(hit_position, 0, 4096, DEFAULT_HIT_POSITION)
    return max(1.0, MAX_TIME_RANGE / speed * position / DEFAULT_HIT_POSITION)


def beat_interval_ms(bpm=120):
    return 60000.0 / bounded_number(bpm, 40, 300, 120)


def sample_note_ages(elapsed_ms, column, keys, bpm=120, duration_ms=574.25):
    """Return ages of currently travelling notes, in milliseconds.

    Each lane gets one note per beat, staggered evenly across the beat. The
    infinite steady-state rhythm avoids an empty canvas just after restart.
    Changing scroll speed affects only the visible time range, not the rhythm.
    """
    interval = beat_interval_ms(bpm)
    offset = column / max(1, keys) * interval
    first = (float(elapsed_ms) - offset) % interval
    if first > duration_ms:
        return ()
    count = int(math.floor((duration_ms-first)/interval)) + 1
    return tuple(first + index*interval for index in range(count))

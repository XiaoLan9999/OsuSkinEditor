"""Deterministic, local-only Mania skin practice session

Tap windows use the stable native-Mania OD formulas and integer rounding from
https://osu.ppy.sh/wiki/en/Gameplay/Judgement/osu!mania
Late MEH hits are impossible: unhit notes expire after the late OK window

This is intentionally a skin tester, not osu!'s score implementation: a hold
has separately scored head/tail events, a premature release misses its tail,
and score is the sum of hit values (320/300/200/100/50/0). Accuracy weights both
PERFECT tiers as 300. There is no health, bonus score, ticks, mods or audio clock
"""

from dataclasses import dataclass, replace
import math
from typing import Iterable


JUDGEMENTS = ("300g", "300", "200", "100", "50", "0")
PATTERNS = ("mixed", "taps", "holds", "chords")
SCORE_VALUES = dict(zip(JUDGEMENTS, (320, 300, 200, 100, 50, 0)))
ACCURACY_VALUES = dict(zip(JUDGEMENTS, (300, 300, 200, 100, 50, 0)))


def _number(value, name, minimum=None, maximum=None):
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    if minimum is not None and result < minimum:
        raise ValueError(f"{name} must be at least {minimum}")
    if maximum is not None and result > maximum:
        raise ValueError(f"{name} must be at most {maximum}")
    return result


def hit_windows(od=5):
    """Return the maximum *rounded* absolute hit error for each judgement"""
    difficulty = _number(od, "OD", 0, 10)
    return dict(zip(JUDGEMENTS, (16, *(int(base - 3*difficulty) for base in
                                     (64, 97, 127, 151, 188)))))


def judge_offset(offset_ms, od=5):
    """Return a judgement key, or None when a keypress is too early

    Rounded absolute offsets use half-up rounding. A positive offset beyond
    the OK window is always a miss, rather than a late MEH
    """
    offset = _number(offset_ms, "Hit offset")
    error = math.floor(abs(offset) + 0.5)
    windows = hit_windows(od)
    if offset > 0 and error > windows["100"]:
        return "0"
    for judgement, window in windows.items():
        if error <= window:
            return judgement
    return None


@dataclass
class PlayNote:
    id: int
    lane: int
    start_ms: float
    end_ms: float | None = None
    status: str = "pending"
    head_judgement: str | None = None

    @property
    def is_hold(self):
        return self.end_ms is not None

    @property
    def column(self):
        return self.lane


@dataclass(frozen=True)
class HitEvent:
    note_id: int
    lane: int
    judgement: str
    offset_ms: float
    time_ms: float
    hold_end: bool = False
    combo: int = 0


def make_practice_chart(keys=4, bpm=120, pattern="mixed", duration_ms=60000,
                        seed=0):
    """Create a repeatable chart with a 2 s lead-in and a 1 s closing gap

    Lane order covers every column, chords use distinct columns, and a new
    note can never intersect an existing hold in the same column
    """
    keys = int(_number(keys, "Keys", 1, 18))
    beat = 60000 / _number(bpm, "BPM", 40, 300)
    duration = _number(duration_ms, "Duration", 4000)
    if pattern not in PATTERNS:
        raise ValueError("Unknown practice pattern")
    notes = []
    occupied_until = [-1.0] * keys
    step = 0
    while (start := 2000 + step*beat/2) < duration - 1000:
        lane = (step + int(seed)) % keys
        lanes = [lane]
        if keys > 1 and (pattern == "chords" or
                         pattern == "mixed" and step % 4 == 2):
            lanes.append((lane + max(1, keys//2)) % keys)
        # Rotate hold textures across phrases while retaining the base lane
        # walk so every column is still exercised even at slow BPM/high keys
        hold_lane = ((step//8 + int(seed)) % keys
                     if pattern == "mixed" and step % 8 == 4 else None)
        if hold_lane is not None and hold_lane not in lanes:
            lanes.append(hold_lane)
        for column in lanes:
            if start < occupied_until[column] + min(200, beat/2):
                continue
            hold = pattern == "holds" or column == hold_lane
            end = start + beat*1.5 if hold else None
            if end is not None and end >= duration - 1000:
                end = None
            notes.append(PlayNote(len(notes), column, start, end))
            occupied_until[column] = end if end is not None else start
        step += 1
    return notes


class ManiaGame:
    """A monotonic practice clock with explicit, non-global keyboard input

    Every advancing/input method returns only the events it generated. The
    caller owns its clock, pause state and displayed event history
    """

    def __init__(self, keys=4, bpm=120, pattern="mixed", od=5,
                 duration_ms=60000, notes: Iterable[PlayNote] | None = None):
        self.od = _number(od, "OD", 0, 10)
        self.duration_ms = _number(duration_ms, "Duration", 4000)
        self.keys = int(_number(keys, "Keys", 1, 18))
        self.bpm = _number(bpm, "BPM", 40, 300)
        self.pattern = pattern
        self.reset(notes=notes)

    def reset(self, keys=None, bpm=None, pattern=None,
              notes: Iterable[PlayNote] | None = None):
        if keys is not None:
            self.keys = int(_number(keys, "Keys", 1, 18))
        if bpm is not None:
            self.bpm = _number(bpm, "BPM", 40, 300)
        if pattern is not None:
            self.pattern = pattern
        if self.pattern not in PATTERNS:
            raise ValueError("Unknown practice pattern")
        source = (make_practice_chart(self.keys, self.bpm, self.pattern,
                                     self.duration_ms) if notes is None else notes)
        self.notes = sorted((replace(note, status="pending", head_judgement=None)
                             for note in source), key=lambda n: (n.start_ms, n.id))
        ids = set()
        occupied_until = [-1.0] * self.keys
        for note in self.notes:
            if note.id in ids:
                raise ValueError("Practice note IDs must be unique")
            ids.add(note.id)
            if not isinstance(note.lane, int) or not 0 <= note.lane < self.keys:
                raise ValueError("Practice note lane is outside the key count")
            note.start_ms = _number(note.start_ms, "Note time", 0)
            if note.end_ms is not None:
                note.end_ms = _number(note.end_ms, "Hold end", note.start_ms)
                if note.end_ms == note.start_ms:
                    raise ValueError("Hold end must follow its head")
            if note.start_ms <= occupied_until[note.lane]:
                raise ValueError("Practice notes overlap within a column")
            occupied_until[note.lane] = (note.end_ms if note.is_hold else
                                         note.start_ms)
        self.now_ms = 0.0
        self.pressed_lanes = set()
        self._auto_pressed_until = {}
        self._autoplay = False
        self.combo = 0
        self.max_combo = 0
        self.score = 0
        self.counts = {key: 0 for key in JUDGEMENTS}
        self.last_event = None

    def set_pattern(self, pattern):
        self.reset(pattern=pattern)

    @property
    def accuracy(self):
        total = sum(self.counts.values())
        return (sum(ACCURACY_VALUES[key]*count for key, count in self.counts.items())
                / (300*total)*100 if total else 100.0)

    @property
    def finished(self):
        return (self.now_ms >= self.duration_ms and
                all(note.status in ("hit", "missed") for note in self.notes))

    @property
    def late_window_ms(self):
        return hit_windows(self.od)["100"] + 0.5

    def _emit(self, note, judgement, offset, timestamp, hold_end=False):
        self.counts[judgement] += 1
        self.score += SCORE_VALUES[judgement]
        self.combo = self.combo + 1 if judgement != "0" else 0
        self.max_combo = max(self.max_combo, self.combo)
        event = HitEvent(note.id, note.lane, judgement, offset, timestamp,
                         hold_end, self.combo)
        self.last_event = event
        return event

    def _head(self, note, judgement, offset, timestamp):
        note.head_judgement = judgement
        note.status = ("missed" if judgement == "0" else
                       "holding" if note.is_hold else "hit")
        return self._emit(note, judgement, offset, timestamp)

    def _tail(self, note, judgement, offset, timestamp):
        note.status = "missed" if judgement == "0" else "hit"
        return self._emit(note, judgement, offset, timestamp, hold_end=True)

    def advance_to(self, ms, autoplay=False):
        """Advance to an absolute session time, independent of frame sizes"""
        target = _number(ms, "Time", self.now_ms)
        events = []
        scheduled = []
        self._autoplay = bool(autoplay)
        for note in self.notes:
            if note.status == "pending":
                at = note.start_ms if autoplay else note.start_ms + self.late_window_ms
                if at <= target:
                    scheduled.append((max(self.now_ms, at), note.id, False, note))
            if note.is_hold and note.status in ("pending", "holding"):
                at = note.end_ms if autoplay else note.end_ms + self.late_window_ms
                if at <= target:
                    scheduled.append((max(self.now_ms, at), note.id, True, note))
        for at, _, tail, note in sorted(scheduled, key=lambda item: item[:3]):
            if tail and note.status == "holding":
                judgement = "300g" if autoplay else "0"
                events.append(self._tail(note, judgement, at - note.end_ms, at))
            elif not tail and note.status == "pending":
                judgement = "300g" if autoplay else "0"
                events.append(self._head(note, judgement, at - note.start_ms, at))
                if autoplay:
                    self._auto_pressed_until[note.lane] = (
                        note.end_ms if note.is_hold else note.start_ms + 80)
        if autoplay:
            # release_all() deliberately clears visual key state on pause
            # Restore an automatic hold when the caller resumes its clock
            for note in self.notes:
                if note.status == "holding":
                    self._auto_pressed_until[note.lane] = note.end_ms
            self.pressed_lanes = {lane for lane, until in self._auto_pressed_until.items()
                                  if until > target}
        self.now_ms = target
        return events

    def key_down(self, lane, ms):
        """Judge the first unjudged note in a lane, once per physical press"""
        if not isinstance(lane, int) or not 0 <= lane < self.keys:
            return []
        events = self.advance_to(ms, autoplay=self._autoplay)
        if self._autoplay or lane in self.pressed_lanes or self.finished:
            return events
        self.pressed_lanes.add(lane)
        # A hold suspended by focus loss may be regripped without a new head
        if any(n.lane == lane and n.status == "holding" for n in self.notes):
            return events
        note = next((n for n in self.notes if n.lane == lane and
                     n.status == "pending"), None)
        if note is not None:
            offset = self.now_ms - note.start_ms
            judgement = judge_offset(offset, self.od)
            if judgement is not None:
                events.append(self._head(note, judgement, offset, self.now_ms))
        return events

    def key_up(self, lane, ms):
        if not isinstance(lane, int) or not 0 <= lane < self.keys:
            return []
        events = self.advance_to(ms, autoplay=self._autoplay)
        if self._autoplay or lane not in self.pressed_lanes:
            return events
        self.pressed_lanes.discard(lane)
        note = next((n for n in self.notes if n.lane == lane and
                     n.status == "holding"), None)
        if note is not None:
            offset = self.now_ms - note.end_ms
            result = judge_offset(offset, self.od)
            # An early body release breaks this simplified tester hold once
            if result is None or result == "0":
                result = "0"
            events.append(self._tail(note, result, offset, self.now_ms))
        return events

    def release_all(self, ms=None):
        """Clear input on pause/focus loss without judging a phantom release

        The caller must pause the clock too. Re-pressing an active hold after
        resuming regrips it without awarding an extra head judgement
        """
        events = self.advance_to(self.now_ms if ms is None else ms,
                                 autoplay=self._autoplay)
        self.pressed_lanes.clear()
        self._auto_pressed_until.clear()
        return events

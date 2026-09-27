import math
import unittest

from core.mania_gameplay import (
    JUDGEMENTS, ManiaGame, PlayNote, hit_windows, judge_offset, make_practice_chart,
)


class ManiaHitWindowsTests(unittest.TestCase):
    def test_native_stable_od_formulas(self):
        self.assertEqual(list(hit_windows(5).values()), [16, 49, 82, 112, 136, 173])
        self.assertEqual(list(hit_windows(0).values()), [16, 64, 97, 127, 151, 188])
        self.assertEqual(list(hit_windows(10).values()), [16, 34, 67, 97, 121, 158])

    def test_every_early_window_boundary_and_rounding(self):
        windows = hit_windows(5)
        for index, (judgement, limit) in enumerate(windows.items()):
            with self.subTest(judgement=judgement):
                self.assertEqual(judge_offset(-limit), judgement)
                self.assertEqual(judge_offset(-limit - 0.499), judgement)
                following = JUDGEMENTS[index + 1] if index < 5 else None
                self.assertEqual(judge_offset(-limit - 0.5), following)

    def test_every_late_window_boundary(self):
        for index, (judgement, limit) in enumerate(list(hit_windows(5).items())[:4]):
            with self.subTest(judgement=judgement):
                self.assertEqual(judge_offset(limit + 0.499), judgement)
                following = JUDGEMENTS[index + 1] if index < 3 else "0"
                self.assertEqual(judge_offset(limit + 0.5), following)
        self.assertEqual(judge_offset(136), "0")
        self.assertEqual(judge_offset(5000), "0")
        self.assertEqual(judge_offset(0), "300g")

    def test_invalid_offsets_or_od_are_rejected(self):
        for value in (math.nan, math.inf, -math.inf):
            with self.assertRaises(ValueError):
                judge_offset(value)
        for value in (-1, 11, math.nan):
            with self.assertRaises(ValueError):
                hit_windows(value)


class ManiaPracticeChartTests(unittest.TestCase):
    def test_all_key_counts_and_patterns_are_repeatable_and_do_not_overlap(self):
        for keys in range(1, 19):
            for pattern in ("mixed", "taps", "holds", "chords"):
                for bpm in (40, 120, 300):
                    with self.subTest(keys=keys, pattern=pattern, bpm=bpm):
                        notes = make_practice_chart(keys, bpm, pattern)
                        self.assertEqual(notes, make_practice_chart(keys, bpm, pattern))
                        self.assertEqual({n.lane for n in notes}, set(range(keys)))
                        end_by_lane = [-1] * keys
                        for note in notes:
                            self.assertGreater(note.start_ms, end_by_lane[note.lane])
                            self.assertGreaterEqual(note.start_ms, 2000)
                            end_by_lane[note.lane] = note.end_ms or note.start_ms
                            self.assertLess(end_by_lane[note.lane], 59000)
                        self.assertEqual(len({n.id for n in notes}), len(notes))

    def test_mixed_4k_exercises_all_hold_textures_and_chords(self):
        notes = make_practice_chart()
        self.assertEqual({n.lane for n in notes if n.is_hold}, {0, 1, 2, 3})
        self.assertTrue(any(sum(n.start_ms == candidate.start_ms for n in notes) > 1
                            for candidate in notes))
        self.assertTrue(any(not n.is_hold for n in notes))

    def test_seed_changes_lane_order_reproducibly(self):
        self.assertNotEqual(make_practice_chart(seed=0), make_practice_chart(seed=1))
        self.assertEqual(make_practice_chart(seed=1), make_practice_chart(seed=1))

    def test_unknown_pattern_and_invalid_configuration_are_rejected(self):
        for args in ({"keys": 0}, {"keys": 19}, {"bpm": 0},
                     {"bpm": math.nan}, {"pattern": "wrong"}):
            with self.assertRaises(ValueError):
                make_practice_chart(**args)


class ManiaGameTests(unittest.TestCase):
    def game(self, *notes, keys=4):
        return ManiaGame(keys=keys, notes=list(notes))

    def test_tap_each_tier_updates_combo_score_and_accuracy(self):
        notes = [PlayNote(i, 0, 1000 + i*1000) for i in range(6)]
        game = self.game(*notes)
        offsets = (0, -30, -65, -95, -125, -150)
        events = []
        for note, offset in zip(notes, offsets):
            at = note.start_ms + offset
            events.extend(game.key_down(0, at))
            game.key_up(0, at + 1)
        self.assertEqual([e.judgement for e in events], list(JUDGEMENTS))
        self.assertEqual([e.combo for e in events], [1, 2, 3, 4, 5, 0])
        self.assertEqual(game.counts, dict.fromkeys(JUDGEMENTS, 1))
        self.assertEqual(game.combo, 0)
        self.assertEqual(game.max_combo, 5)
        self.assertEqual(game.score, 970)
        self.assertAlmostEqual(game.accuracy, 950/1800*100)
        self.assertEqual(game.last_event, events[-1])

    def test_ignored_early_press_does_not_hit_when_held_down(self):
        game = self.game(PlayNote(0, 0, 1000))
        self.assertEqual(game.key_down(0, 800), [])
        self.assertEqual(game.key_down(0, 1000), [])
        self.assertEqual(game.notes[0].status, "pending")
        game.key_up(0, 1001)
        self.assertEqual(game.key_down(0, 1002)[0].judgement, "300g")

    def test_auto_repeat_never_hits_next_note(self):
        game = self.game(PlayNote(0, 0, 1000), PlayNote(1, 0, 1100))
        self.assertEqual(len(game.key_down(0, 1000)), 1)
        self.assertEqual(game.key_down(0, 1100), [])
        self.assertEqual(game.notes[1].status, "pending")
        game.key_up(0, 1100)
        self.assertEqual(game.key_down(0, 1101)[0].note_id, 1)

    def test_press_does_not_steal_the_next_note_in_same_column(self):
        game = self.game(PlayNote(0, 0, 1000), PlayNote(1, 0, 1080))
        event = game.key_down(0, 1070)[0]
        self.assertEqual(event.note_id, 0)
        self.assertEqual(event.judgement, "200")
        self.assertEqual(game.notes[1].status, "pending")

    def test_deadline_misses_once_and_late_input_can_hit_following_note(self):
        game = self.game(PlayNote(0, 0, 1000), PlayNote(1, 0, 1150))
        events = game.key_down(0, 1150)
        self.assertEqual([(e.note_id, e.judgement) for e in events],
                         [(0, "0"), (1, "300g")])
        self.assertEqual(events[0].time_ms, 1112.5)
        self.assertEqual(game.advance_to(8000), [])
        self.assertEqual(game.counts["0"], 1)

    def test_hit_exactly_at_late_ok_boundary(self):
        game = self.game(PlayNote(0, 0, 1000))
        self.assertEqual(game.key_down(0, 1112.499)[0].judgement, "100")
        other = self.game(PlayNote(0, 0, 1000))
        self.assertEqual(other.key_down(0, 1112.5)[0].judgement, "0")
        self.assertEqual(sum(other.counts.values()), 1)

    def test_chord_is_independently_judged_per_column(self):
        game = self.game(*(PlayNote(i, i, 1000) for i in range(4)))
        for lane in (2, 0, 3, 1):
            self.assertEqual(game.key_down(lane, 1000)[0].lane, lane)
        self.assertEqual(game.combo, 4)
        self.assertEqual(game.pressed_lanes, {0, 1, 2, 3})

    def test_invalid_lane_has_no_effect(self):
        game = self.game(PlayNote(0, 0, 1000))
        for lane in (-1, 4, "0", 1.5):
            self.assertEqual(game.key_down(lane, 1000), [])
            self.assertEqual(game.key_up(lane, 1000), [])
        self.assertEqual(game.now_ms, 0)
        self.assertEqual(game.pressed_lanes, set())

    def test_successful_hold_scores_head_and_release_tail(self):
        game = self.game(PlayNote(0, 0, 1000, 2000))
        head = game.key_down(0, 1000)[0]
        self.assertFalse(head.hold_end)
        self.assertEqual(game.notes[0].status, "holding")
        self.assertEqual(game.advance_to(1950), [])
        tail = game.key_up(0, 2030)[0]
        self.assertTrue(tail.hold_end)
        self.assertEqual(tail.judgement, "300")
        self.assertEqual(tail.offset_ms, 30)
        self.assertEqual(game.notes[0].status, "hit")
        self.assertEqual(game.combo, 2)
        self.assertEqual(game.accuracy, 100)

    def test_hold_early_release_breaks_once_and_repress_cannot_revive_it(self):
        game = self.game(PlayNote(0, 0, 1000, 2000))
        game.key_down(0, 1000)
        events = game.key_up(0, 1300)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].judgement, "0")
        self.assertTrue(events[0].hold_end)
        self.assertEqual(game.combo, 0)
        self.assertEqual(game.key_down(0, 1900), [])
        self.assertEqual(game.key_up(0, 2000), [])
        self.assertEqual(game.advance_to(3000), [])
        self.assertEqual(game.counts["0"], 1)

    def test_tail_early_meh_is_allowed_but_late_meh_is_miss(self):
        for offset, expected in ((-130, "50"), (112.49, "100"), (113, "0")):
            with self.subTest(offset=offset):
                game = self.game(PlayNote(0, 0, 1000, 2000))
                game.key_down(0, 1000)
                event = game.key_up(0, 2000 + offset)[0]
                self.assertEqual(event.judgement, expected)
                self.assertTrue(event.hold_end)

    def test_missed_head_does_not_also_miss_tail(self):
        game = self.game(PlayNote(0, 0, 1000, 2000))
        events = game.advance_to(3000)
        self.assertEqual(len(events), 1)
        self.assertFalse(events[0].hold_end)
        self.assertEqual(game.counts["0"], 1)

    def test_never_released_hold_expires_tail_once(self):
        game = self.game(PlayNote(0, 0, 1000, 2000))
        game.key_down(0, 1000)
        events = game.advance_to(3000)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].time_ms, 2112.5)
        self.assertTrue(events[0].hold_end)
        self.assertEqual(game.key_up(0, 3100), [])
        self.assertEqual(game.counts["0"], 1)

    def test_focus_loss_clears_keys_without_phantom_release_and_hold_regrips(self):
        game = self.game(PlayNote(0, 0, 1000, 2000))
        game.key_down(0, 1000)
        self.assertEqual(game.release_all(1400), [])
        self.assertEqual(game.pressed_lanes, set())
        self.assertEqual(game.notes[0].status, "holding")
        self.assertEqual(game.key_up(0, 1400), [])
        self.assertEqual(game.key_down(0, 1450), [])
        self.assertEqual(game.key_up(0, 2000)[0].judgement, "300g")
        self.assertEqual(sum(game.counts.values()), 2)

    def test_autoplay_is_frame_step_independent_and_includes_hold_tails(self):
        whole = ManiaGame()
        whole_events = whole.advance_to(60000, autoplay=True)
        stepped = ManiaGame()
        stepped_events = []
        for timestamp in range(0, 60000, 37):
            stepped_events.extend(stepped.advance_to(timestamp, autoplay=True))
        stepped_events.extend(stepped.advance_to(60000, autoplay=True))
        self.assertEqual(whole_events, stepped_events)
        self.assertEqual(whole.notes, stepped.notes)
        expected = sum(2 if n.is_hold else 1 for n in whole.notes)
        self.assertEqual(len(whole_events), expected)
        self.assertEqual(whole.counts["300g"], expected)
        self.assertEqual(whole.combo, expected)
        self.assertEqual(whole.accuracy, 100)
        self.assertTrue(whole.finished)
        self.assertEqual(whole.pressed_lanes, set())

    def test_autoplay_pressed_visual_duration_and_manual_input_is_ignored(self):
        game = self.game(PlayNote(0, 0, 1000), PlayNote(1, 1, 1000, 2000))
        game.advance_to(1000, autoplay=True)
        self.assertEqual(game.pressed_lanes, {0, 1})
        self.assertEqual(game.key_down(0, 1010), [])
        game.advance_to(1080, autoplay=True)
        self.assertEqual(game.pressed_lanes, {1})
        game.advance_to(2000, autoplay=True)
        self.assertEqual(game.pressed_lanes, set())
        self.assertEqual(game.combo, 3)

    def test_manual_misses_are_frame_step_independent(self):
        whole = ManiaGame()
        expected = whole.advance_to(60000)
        stepped = ManiaGame()
        actual = []
        for timestamp in range(0, 60000, 73):
            actual.extend(stepped.advance_to(timestamp))
        actual.extend(stepped.advance_to(60000))
        self.assertEqual(actual, expected)

    def test_autoplay_hold_visual_restores_after_pause(self):
        game = self.game(PlayNote(0, 0, 1000, 2000))
        game.advance_to(1000, autoplay=True)
        game.release_all()
        self.assertEqual(game.pressed_lanes, set())
        game.advance_to(1500, autoplay=True)
        self.assertEqual(game.pressed_lanes, {0})
        self.assertEqual(game.advance_to(2000, autoplay=True)[0].judgement, "300g")

    def test_time_must_be_finite_and_monotonic(self):
        game = ManiaGame()
        game.advance_to(500)
        for bad in (499, math.nan, math.inf):
            with self.assertRaises(ValueError):
                game.advance_to(bad)

    def test_reset_clears_every_session_state_and_changes_chart(self):
        game = ManiaGame()
        game.advance_to(30000, autoplay=True)
        game.reset(keys=7, bpm=180, pattern="taps")
        self.assertEqual(game.now_ms, 0)
        self.assertEqual(game.keys, 7)
        self.assertEqual(game.bpm, 180)
        self.assertEqual(game.pattern, "taps")
        self.assertEqual(game.score, 0)
        self.assertEqual(game.combo, 0)
        self.assertEqual(game.max_combo, 0)
        self.assertEqual(sum(game.counts.values()), 0)
        self.assertEqual(game.accuracy, 100)
        self.assertEqual(game.pressed_lanes, set())
        self.assertIsNone(game.last_event)
        self.assertFalse(game.finished)
        self.assertTrue(all(n.status == "pending" and not n.is_hold for n in game.notes))
        game.set_pattern("holds")
        self.assertTrue(any(n.is_hold for n in game.notes))

    def test_custom_notes_are_copied_and_validated(self):
        original = PlayNote(0, 0, 1000)
        game = self.game(original)
        game.key_down(0, 1000)
        self.assertEqual(original.status, "pending")
        for notes in ([PlayNote(0, 4, 1000)],
                      [PlayNote(0, 0, math.nan)],
                      [PlayNote(0, 0, 1000, 999)],
                      [PlayNote(0, 0, 1000, 1000)],
                      [PlayNote(0, 0, 1000, 2000), PlayNote(1, 0, 1500)],
                      [PlayNote(0, 0, 1000), PlayNote(0, 1, 1000)]):
            with self.subTest(notes=notes), self.assertRaises(ValueError):
                self.game(*notes)


if __name__ == "__main__":
    unittest.main()

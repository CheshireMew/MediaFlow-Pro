from types import SimpleNamespace

from mediaflow.desktop.controllers.timeline_snap_index import TimelineSnapTargetIndex


def _item(item_id: str, start: int, end: int) -> SimpleNamespace:
    return SimpleNamespace(
        id=item_id,
        timeline_start=start,
        timeline_end=end,
        frame=start,
        start_frame=start,
        end_frame=end,
    )


def test_snap_index_queries_a_small_sorted_window_and_honors_exclusions() -> None:
    index = TimelineSnapTargetIndex()
    first = _item("clip-a", 10, 20)
    second = _item("clip-b", 30, 40)
    state = SimpleNamespace(
        clips=[first, second],
        markers=[_item("marker-a", 50, 50)],
        ranges=[_item("range-a", 60, 70)],
    )
    subtitles = [
        {
            "placementId": "subtitle-a",
            "startFrame": 80,
            "endFrame": 90,
        }
    ]

    index.rebuild(state, subtitles, 4)

    assert index.target_count == 9
    assert index.is_current(state, 4)
    assert index.snap(
        31,
        2,
        playhead_frame=100,
        excluded_clip_ids=set(),
        excluded_subtitle_ids=set(),
    ) == 30
    assert index.snap(
        31,
        2,
        playhead_frame=100,
        excluded_clip_ids={"clip-b"},
        excluded_subtitle_ids=set(),
    ) == 31
    assert index.snap(
        81,
        2,
        playhead_frame=100,
        excluded_clip_ids=set(),
        excluded_subtitle_ids={"subtitle-a"},
    ) == 81


def test_snap_index_updates_clip_edges_without_rebuilding_subtitle_targets() -> None:
    index = TimelineSnapTargetIndex()
    source = _item("clip-a", 10, 20)
    state = SimpleNamespace(clips=[source], markers=[], ranges=[])
    subtitles = [
        {
            "placementId": "subtitle-a",
            "startFrame": 80,
            "endFrame": 90,
        }
    ]
    index.rebuild(state, subtitles, 1)
    changed = _item("clip-a", 30, 40)
    changed_state = SimpleNamespace(clips=[changed], markers=[], ranges=[])

    index.update_clips(changed_state, [changed])

    assert index.is_current(changed_state, 1)
    assert index.snap(
        39,
        2,
        playhead_frame=0,
        excluded_clip_ids=set(),
        excluded_subtitle_ids=set(),
    ) == 40
    assert index.snap(
        81,
        2,
        playhead_frame=0,
        excluded_clip_ids=set(),
        excluded_subtitle_ids=set(),
    ) == 80

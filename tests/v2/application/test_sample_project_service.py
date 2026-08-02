from pathlib import Path

from mediaflow.composition import EditorApplication


def test_sample_project_is_visible_through_the_real_project_and_render_boundaries(
    tmp_path: Path,
) -> None:
    application = EditorApplication()
    root = tmp_path / "MediaFlow Sample"
    project = application.create_project(root, "MediaFlow Sample")
    try:
        project.populate_sample_project()
        model = project.get_project()
        assets = project.list_assets()
        sequences = project.list_sequences()
        timeline = project.timeline(model.main_sequence_id).state

        assert [asset.name for asset in assets] == [
            "开场 · 工作台",
            "主体 · 内容节奏",
            "收束 · 准备导出",
        ]
        assert len(project.list_asset_bins()) == 2
        assert [sequence.name for sequence in sequences] == ["主序列", "竖屏精选"]
        assert len(timeline.clips) == 3
        assert len(timeline.transitions) == 2
        assert len(timeline.markers) == 2
        assert len(timeline.ranges) == 1
        assert any(clip.visual_effects for clip in timeline.clips)
        for asset in assets:
            path = project.resolve_asset_path(asset)
            assert path.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")

        preview = application.write_preview_snapshot(
            root,
            timeline,
            use_proxies=False,
            prefer_sdr_preview_proxy=False,
        )
        xml = preview.read_text(encoding="utf-8")
        assert "visual_effect_" in xml
        assert "transition_mix_" in xml
        assert all(str(project.resolve_asset_path(asset)) in xml for asset in assets)
    finally:
        project.close()

    reopened = application.open_project(root)
    try:
        timeline = reopened.timeline(reopened.get_project().main_sequence_id).state
        assert len(timeline.clips) == 3
        assert any(clip.visual_effects for clip in timeline.clips)
    finally:
        reopened.close()

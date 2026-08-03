from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
RUNTIME_HEADER = ROOT / "mediaflow" / "desktop" / "native" / "MltRuntime.h"
RUNTIME_SOURCE = ROOT / "mediaflow" / "desktop" / "native" / "MltRuntime.cpp"
PREVIEW_SOURCE = ROOT / "mediaflow" / "desktop" / "native" / "MltPreviewItem.cpp"


def test_native_preview_separates_audio_clock_from_bounded_video_predecode() -> None:
    runtime_header = RUNTIME_HEADER.read_text(encoding="utf-8")
    runtime_source = RUNTIME_SOURCE.read_text(encoding="utf-8")
    preview_source = PREVIEW_SOURCE.read_text(encoding="utf-8")
    implementation = runtime_header + runtime_source + preview_source

    assert "m_audioProducer" in runtime_header
    assert "m_videoProducer" in runtime_header
    assert 'factoryConsumer(m_audioProfile, "sdl2_audio"' in runtime_source
    assert 'factoryConsumer(m_videoProfile, "null"' in runtime_source
    assert 'propertiesSetInt(audioProperties, "video_off", 1)' in runtime_source
    assert 'propertiesSetInt(videoProperties, "audio_off", 1)' in runtime_source
    assert 'propertiesSetInt(videoProperties, "real_time", 0)' in runtime_source
    assert runtime_source.count('"consumer-frame-show"') == 2
    assert "&MltRuntime::onVideoFrameShown" in runtime_source
    assert "&MltRuntime::onAudioFrameShown" in runtime_source
    assert "m_playbackConsumersActive.store(true" in runtime_source
    assert "waitForConsumerCallbacks();" in runtime_source
    assert "m_consumerCallbacksInFlight" in runtime_header
    assert "m_renderQueueNotFull.wait" in runtime_source
    assert "qBound(24, qRound(m_fps), 60)" in runtime_source
    assert "m_audioClockPosition.store(position" in runtime_source
    assert "runtime->beginPresentation(generation)" in runtime_source
    assert "m_cadenceClock.start()" in runtime_source
    assert "m_nextCadenceDeadlineNs = qMin(" in runtime_source
    assert "m_nextCadenceDeadlineNs + nominalIntervalNs" in runtime_source
    assert "nowNs + nominalIntervalNs" in runtime_source
    assert "lagFrames > allowedLeadFrames" in runtime_source
    assert "qFloor(500.0 / m_fps)" in runtime_source
    assert "m_nextCadenceDeadlineNs = nowNs" not in runtime_source
    assert '"consumer-frame-render"' not in runtime_source
    resize_boundary = runtime_source.split(
        "void MltRuntime::setPreviewSize", 1
    )[1].split("void MltRuntime::close", 1)[0]
    assert "consumerProperties" not in resize_boundary

    removed_shared_consumer_clock = (
        "onConsumerFrameRendered",
        "onConsumerPlaybackStarted",
        "m_videoRenderEvent",
        "m_presentationClock",
        "m_nextPresentationDeadlineNs",
        "m_renderSequence",
        "m_videoWaitClock",
        "m_waitingForVideoFrame",
        "presentationDeadlineMissed",
    )
    assert all(name not in implementation for name in removed_shared_consumer_clock)

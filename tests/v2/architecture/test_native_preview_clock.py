from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
RUNTIME_HEADER = ROOT / "mediaflow" / "desktop" / "native" / "MltRuntime.h"
RUNTIME_SOURCE = ROOT / "mediaflow" / "desktop" / "native" / "MltRuntime.cpp"
PREVIEW_SOURCE = ROOT / "mediaflow" / "desktop" / "native" / "MltPreviewItem.cpp"
PREVIEW_HEADER = ROOT / "mediaflow" / "desktop" / "native" / "MltPreviewItem.h"
PREVIEW_QML = ROOT / "mediaflow" / "desktop" / "qml" / "components" / "PreviewViewport.qml"


def test_native_preview_uses_one_audio_clock_consumer_for_audio_and_video() -> None:
    runtime_header = RUNTIME_HEADER.read_text(encoding="utf-8")
    runtime_source = RUNTIME_SOURCE.read_text(encoding="utf-8")
    preview_source = PREVIEW_SOURCE.read_text(encoding="utf-8")
    implementation = runtime_header + runtime_source + preview_source

    assert "m_previewProducer" in runtime_header
    assert "m_videoProducer" not in runtime_header
    assert "m_videoConsumer" not in runtime_header
    assert "m_videoProfile" not in runtime_header
    assert 'factoryConsumer(m_previewProfile, "sdl2_audio"' in runtime_source
    assert 'factoryConsumer(m_previewProfile, "rtaudio"' in runtime_source
    assert '"libmltqt6:libmltglaxnimate-qt6:libmltopencv"' in runtime_source
    assert 'factoryConsumer(m_videoProfile, "null"' not in runtime_source
    assert 'propertiesSetInt(previewProperties, "video_off", 0)' in runtime_source
    assert 'propertiesSetInt(previewProperties, "width", m_previewWidth)' in runtime_source
    assert 'propertiesSetInt(previewProperties, "height", m_previewHeight)' in runtime_source
    assert runtime_source.count('"consumer-frame-show"') == 1
    assert "&MltRuntime::onFrameShown" in runtime_source
    assert "m_playbackConsumerActive.store(true" in runtime_source
    assert "waitForConsumerCallbacks();" in runtime_source
    assert "m_consumerCallbacksInFlight" in runtime_header
    close_boundary = runtime_source.split(
        "void MltRuntime::closePlaybackConsumer", 1
    )[1].split("bool MltRuntime::beginConsumerCallback", 1)[0]
    assert close_boundary.index("consumerStop") < close_boundary.index(
        "producerSetSpeed"
    )
    assert close_boundary.index("producerSetSpeed") < close_boundary.index(
        "waitForConsumerCallbacks"
    )
    assert close_boundary.index("waitForConsumerCallbacks") < close_boundary.index(
        "eventsDisconnect"
    )
    assert close_boundary.index("eventsDisconnect") < close_boundary.index(
        "consumerClose"
    )
    assert "consumerPurge" not in implementation
    assert "m_renderQueueNotFull.wait" in runtime_source
    assert "qBound(24, qRound(m_fps), 60)" in runtime_source
    frame_boundary = runtime_source.split(
        "void MltRuntime::onFrameShown", 1
    )[1].split("void MltRuntime::beginPresentation", 1)[0]
    assert frame_boundary.index("readFrameImage") < frame_boundary.index(
        "m_audioClockPosition.store(position"
    )
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


def test_native_preview_exposes_buffering_without_clearing_play_intent() -> None:
    runtime_header = RUNTIME_HEADER.read_text(encoding="utf-8")
    runtime_source = RUNTIME_SOURCE.read_text(encoding="utf-8")
    preview_header = PREVIEW_HEADER.read_text(encoding="utf-8")
    preview_qml = PREVIEW_QML.read_text(encoding="utf-8")

    assert "bufferStateChanged(bool buffering, int bufferedFrames" in runtime_header
    assert "setBufferState(true, queuedFrames)" in runtime_source
    assert "setBufferState(false, 0)" in runtime_source
    assert "setBufferState(false, queuedFrames)" not in runtime_source
    missing_frame_branch = runtime_source.split("if (!frameReady)", 1)[1].split(
        "setBufferState(false, 0)", 1
    )[0]
    assert "setPlaying(false)" not in missing_frame_branch
    assert "Q_PROPERTY(bool buffering" in preview_header
    assert "Q_PROPERTY(int bufferedFrames" in preview_header
    assert "interval: 300" in preview_qml
    assert "visible: false" in preview_qml
    assert "onTriggered: bufferingNotice.visible = preview.buffering" in preview_qml

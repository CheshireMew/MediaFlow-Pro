from __future__ import annotations

from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

from mediaflow.infrastructure.runtime_context import RuntimeContext
from mediaflow.infrastructure.web_browser import WebPackagePreviewServer

FIXTURE = Path("tests/fixtures/editable-media-v6")


def test_real_v6_runtime_enforces_frame_readiness_and_latest_seek() -> None:
    with (
        WebPackagePreviewServer(FIXTURE) as preview,
        sync_playwright() as playwright,
    ):
        browser = playwright.chromium.launch(
            executable_path=str(RuntimeContext.discover().paths.chromium),
            headless=True,
            args=["--disable-gpu"],
        )
        page = browser.new_page(viewport={"width": 720, "height": 1280})
        page.goto(preview.url_for("index.html"))
        result = page.evaluate(
            """async () => {
                await window.editableMedia.ready;
                let mode = "success";
                window.__hf.registerRenderer(
                    "mediaflow-v6-protocol-test",
                    ({seconds}) => {
                        if (mode === "success") {
                            const handle = window.__hf.deferFrame({
                                label: "resolved-task",
                                timeout_ms: 100,
                            });
                            setTimeout(() => window.__hf.resolveFrame(handle), 5);
                            return;
                        }
                        if (mode === "timeout") {
                            window.__hf.deferFrame({
                                label: "timed-out-task",
                                timeout_ms: 5,
                            });
                            return;
                        }
                        if (mode === "reject") {
                            const handle = window.__hf.deferFrame({
                                label: "rejected-task",
                            });
                            window.__hf.rejectFrame(handle, {
                                code: "frame_task_failed",
                                message: "intentional rejection",
                            });
                            return;
                        }
                        if (mode === "retryable-reject") {
                            const handle = window.__hf.deferFrame({
                                label: "retryable-rejected-task",
                            });
                            window.__hf.rejectFrame(handle, {
                                code: "frame_task_failed",
                                message: "intentional retryable rejection",
                                retryable: true,
                            });
                            return;
                        }
                        if (mode === "supersede" && seconds < 0.5) {
                            return new Promise((resolve) => setTimeout(resolve, 100));
                        }
                    },
                );
                const serialize = (error) => (
                    typeof error?.toJSON === "function"
                        ? error.toJSON()
                        : {
                            code: error?.code,
                            message: error?.message,
                            seconds: error?.seconds,
                            generation: error?.generation,
                            label: error?.label,
                            retryable: error?.retryable,
                        }
                );
                const captureFailure = async (seconds) => {
                    try {
                        await window.__hf.seek(seconds);
                        return null;
                    } catch (error) {
                        return serialize(error);
                    }
                };

                const success = await window.__hf.seek(0.1);
                mode = "timeout";
                const timeout = await captureFailure(0.2);
                mode = "reject";
                const rejection = await captureFailure(0.3);
                mode = "retryable-reject";
                const retryableRejection = await captureFailure(0.4);
                mode = "supersede";
                const firstSeek = window.__hf.seek(0.25).then(
                    () => null,
                    (error) => serialize(error),
                );
                await new Promise((resolve) => setTimeout(resolve, 5));
                const latest = await window.__hf.seek(0.75);
                const superseded = await firstSeek;
                return {success, timeout, rejection, retryableRejection, superseded, latest};
            }"""
        )
        browser.close()

    success = result["success"]
    assert success["seconds"] == pytest.approx(0.1)
    assert success["generation"] > 0
    assert success["wait_ms"] >= 5
    assert len(success["tasks"]) == 1
    assert success["tasks"][0]["label"] == "resolved-task"
    assert success["tasks"][0]["elapsed_ms"] >= 5

    timeout = result["timeout"]
    assert timeout["code"] == "frame_task_timeout"
    assert timeout["seconds"] == pytest.approx(0.2)
    assert timeout["generation"] == success["generation"] + 1
    assert timeout["label"] == "timed-out-task"
    assert timeout["retryable"] is True
    assert "timed out" in timeout["message"]

    rejection = result["rejection"]
    assert rejection["code"] == "frame_task_failed"
    assert rejection["seconds"] == pytest.approx(0.3)
    assert rejection["generation"] == timeout["generation"] + 1
    assert rejection["label"] == "rejected-task"
    assert rejection["retryable"] is False

    retryable = result["retryableRejection"]
    assert retryable["code"] == "frame_task_failed"
    assert retryable["seconds"] == pytest.approx(0.4)
    assert retryable["generation"] == rejection["generation"] + 1
    assert retryable["label"] == "retryable-rejected-task"
    assert retryable["retryable"] is True

    superseded = result["superseded"]
    assert superseded["code"] == "frame_superseded"
    assert superseded["seconds"] == pytest.approx(0.25)
    assert superseded["generation"] == retryable["generation"] + 1
    assert superseded["label"] is None
    assert superseded["retryable"] is False
    assert result["latest"]["seconds"] == pytest.approx(0.75)
    assert result["latest"]["generation"] == superseded["generation"] + 1

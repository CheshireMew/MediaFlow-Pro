from __future__ import annotations

from pathlib import Path

from mediaflow.infrastructure.gpt_sovits_engine import (
    GptSoVitsEngine,
    GptSoVitsResult,
)


class _Process:
    def __init__(self) -> None:
        self.stdout: list[str] = []
        self.returncode: int | None = None
        self.terminated = False

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = 0

    def wait(self, timeout: float | None = None) -> int:
        return self.returncode or 0

    def kill(self) -> None:
        self.returncode = -9


def test_batch_session_starts_one_server_for_multiple_utterances(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = tmp_path / "GPT-SoVITS"
    (root / "runtime").mkdir(parents=True)
    (root / "runtime" / "python.exe").write_bytes(b"python")
    (root / "api_v2.py").write_text("# fixture", encoding="utf-8")
    reference = tmp_path / "reference.wav"
    reference.write_bytes(b"reference")
    process = _Process()
    launches: list[list[str]] = []
    calls: list[tuple[int, str]] = []

    def popen(command, **_arguments):
        launches.append([str(item) for item in command])
        return process

    def synthesize_with_server(self, port, **arguments):
        calls.append((port, str(arguments["text"])))
        output = Path(arguments["output_path"])
        return GptSoVitsResult(
            output_path=output,
            sha256="0" * 64,
            duration_seconds=1.0,
            sample_rate=32_000,
            channels=1,
            reference_audio_sha256="1" * 64,
            device="cpu",
        )

    monkeypatch.setattr(
        "mediaflow.infrastructure.gpt_sovits_engine.subprocess.Popen",
        popen,
    )
    monkeypatch.setattr(
        GptSoVitsEngine,
        "_wait_until_ready",
        lambda self, current, port, captured: None,
    )
    monkeypatch.setattr(
        GptSoVitsEngine,
        "_synthesize_with_server",
        synthesize_with_server,
    )
    engine = GptSoVitsEngine(
        root,
        tmp_path / "runtime-data",
        device="cpu",
    )

    with engine.session() as session:
        for index, text in enumerate(("第一句", "第二句"), start=1):
            session.synthesize(
                text=text,
                text_language="zh",
                reference_audio=reference,
                reference_text="reference",
                reference_language="en",
                output_path=tmp_path / f"line-{index}.wav",
            )

    assert len(launches) == 1
    assert [text for _port, text in calls] == ["第一句", "第二句"]
    assert len({port for port, _text in calls}) == 1
    assert process.terminated is True

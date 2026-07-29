import threading

from mediaflow.application.events import TaskEvent, TaskEventBus


def test_subscriber_receives_snapshot_before_live_events() -> None:
    snapshot = TaskEvent("one", "project", "snapshot", 2)
    bus = TaskEventBus(lambda: [snapshot])
    received: list[TaskEvent] = []

    token = bus.subscribe(received.append)
    live = TaskEvent("one", "project", "progress", 3, {"progress": 50})
    bus.publish(live)

    assert received == [snapshot, live]
    bus.unsubscribe(token)
    assert bus.subscriber_count == 0


def test_snapshot_delivery_is_serialized_before_concurrent_live_events() -> None:
    snapshot_started = threading.Event()
    release_snapshot = threading.Event()
    received: list[str] = []

    def handler(event: TaskEvent) -> None:
        received.append(event.task_id)
        if event.task_id == "snapshot-1":
            snapshot_started.set()
            assert release_snapshot.wait(5)

    bus = TaskEventBus(
        lambda: [
            TaskEvent("snapshot-1", "project", "snapshot", 1),
            TaskEvent("snapshot-2", "project", "snapshot", 1),
        ]
    )
    subscriber = threading.Thread(target=lambda: bus.subscribe(handler))
    subscriber.start()
    assert snapshot_started.wait(5)

    publisher = threading.Thread(
        target=lambda: bus.publish(TaskEvent("live", "project", "progress", 2))
    )
    publisher.start()
    release_snapshot.set()
    subscriber.join(5)
    publisher.join(5)

    assert received == ["snapshot-1", "snapshot-2", "live"]

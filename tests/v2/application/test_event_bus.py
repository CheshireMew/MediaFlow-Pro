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

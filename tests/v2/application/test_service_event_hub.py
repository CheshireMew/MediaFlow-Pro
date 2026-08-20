from __future__ import annotations

import asyncio

import pytest

from mediaflow.service.events import EVENT_STREAM_OVERFLOW, EventHub, ServiceEvent


@pytest.mark.asyncio
async def test_event_hub_filters_before_events_enter_a_subscriber_queue() -> None:
    hub = EventHub(asyncio.get_running_loop())
    _token, queue = hub.subscribe(
        selector=lambda event: event.payload.get("project_id") == "project-a",
        capacity=2,
    )

    for cursor in range(20):
        hub.publish(
            ServiceEvent(
                "project.changed",
                {"project_id": "project-b", "cursor": cursor},
            )
        )
    hub.publish(
        ServiceEvent(
            "project.changed",
            {"project_id": "project-a", "cursor": 21},
        )
    )

    assert queue.qsize() == 1
    assert (await queue.get()).payload == {"project_id": "project-a", "cursor": 21}


@pytest.mark.asyncio
async def test_event_hub_bounds_a_slow_subscriber_and_requires_durable_replay() -> None:
    hub = EventHub(asyncio.get_running_loop())
    _token, queue = hub.subscribe(capacity=2)

    hub.publish(ServiceEvent("task.changed", {"cursor": 1}))
    hub.publish(ServiceEvent("task.changed", {"cursor": 2}))
    hub.publish(ServiceEvent("task.changed", {"cursor": 3}))

    assert queue.qsize() == 1
    overflow = await queue.get()
    assert overflow.type == EVENT_STREAM_OVERFLOW
    assert overflow.payload == {
        "reason": "subscriber_queue_full",
        "subscription": 1,
        "capacity": 2,
        "recovery": "reconnect_with_committed_cursors",
    }

    hub.publish(ServiceEvent("task.changed", {"cursor": 4}))
    assert queue.empty()


@pytest.mark.asyncio
async def test_event_hub_replaces_an_overflow_with_service_shutdown() -> None:
    hub = EventHub(asyncio.get_running_loop())
    _token, queue = hub.subscribe(capacity=1)
    hub.publish(ServiceEvent("task.changed", {"cursor": 1}))
    hub.publish(ServiceEvent("task.changed", {"cursor": 2}))

    hub.publish(ServiceEvent("service.stopping", {}))

    assert queue.qsize() == 1
    assert (await queue.get()).type == "service.stopping"

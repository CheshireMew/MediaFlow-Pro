import QtQuick

QtObject {
    id: root
    required property var sourceModel
    property var rows: []

    function reset() {
        rows = sourceModel.overview();
    }

    function patch(changedRows, removedIds) {
        const removed = {};
        const changed = {};
        for (const clipId of removedIds)
            removed[String(clipId)] = true;
        for (const row of changedRows)
            changed[String(row.clipId)] = row;
        const nextRows = [];
        for (const row of rows) {
            const clipId = String(row.clipId);
            if (removed[clipId] === true)
                continue;
            if (changed[clipId] !== undefined) {
                nextRows.push(changed[clipId]);
                delete changed[clipId];
            } else {
                nextRows.push(row);
            }
        }
        for (const clipId in changed)
            nextRows.push(changed[clipId]);
        rows = nextRows;
    }

    Component.onCompleted: reset()
    property Connections subscription: Connections {
        target: root.sourceModel
        function onSourceItemsReset() { root.reset(); }
        function onSourceItemsPatched(changedRows, removedIds) {
            root.patch(changedRows, removedIds);
        }
    }
}

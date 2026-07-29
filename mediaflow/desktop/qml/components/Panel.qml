import QtQuick
import ".."

Rectangle {
    id: root
    property int level: 0
    property bool selected: false
    property bool interactive: false

    color: selected
        ? Theme.selectionSoft
        : level >= 2
        ? Theme.surfaceFloating
        : level === 1
        ? Theme.surfaceRaised
        : Theme.surface
    border.color: selected
        ? Theme.accent
        : level >= 2
        ? Theme.borderStrong
        : level === 1
        ? Theme.border
        : Theme.borderSubtle
    border.width: 1
    radius: level >= 2 ? Theme.radiusLarge : Theme.radius

    Behavior on color {
        enabled: root.interactive
        ColorAnimation { duration: Theme.durationFast }
    }
    Behavior on border.color {
        enabled: root.interactive
        ColorAnimation { duration: Theme.durationFast }
    }
}

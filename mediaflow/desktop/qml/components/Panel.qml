import QtQuick
import ".."

Rectangle {
    property int level: 0

    color: level >= 2 ? Theme.surfaceFloating : level === 1 ? Theme.surfaceRaised : Theme.surface
    border.color: level >= 1 ? Theme.borderStrong : Theme.border
    border.width: 1
    radius: level >= 2 ? Theme.radiusLarge : Theme.radius
}

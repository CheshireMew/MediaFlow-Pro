import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import ".."

Rectangle {
    id: controls
    objectName: "previewControlBar"
    required property var viewport
    required property var preview
    anchors.left: parent.left
    anchors.right: parent.right
    anchors.bottom: parent.bottom
    height: 44
    color: Theme.surface

    Rectangle {
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        height: 1
        color: Theme.divider
    }

    Flickable {
        id: previewControlsFlick
        objectName: "previewControlsScroll"
        anchors.fill: controls
        clip: true
        contentWidth: Math.max(width, previewControls.implicitWidth + 24)
        contentHeight: previewControls.implicitHeight
        flickableDirection: Flickable.HorizontalFlick
        boundsBehavior: Flickable.StopAtBounds
        interactive: contentWidth > width
        onWidthChanged: Qt.callLater(function () {
            contentX = Math.max(0, Math.min(contentX, Math.max(0, contentWidth - width)));
        })
        onContentWidthChanged: Qt.callLater(function () {
            contentX = Math.max(0, Math.min(contentX, Math.max(0, contentWidth - width)));
        })
        ScrollBar.horizontal: AppScrollBar {
            policy: previewControlsFlick.contentWidth > previewControlsFlick.width ? ScrollBar.AlwaysOn : ScrollBar.AlwaysOff
        }

        RowLayout {
            id: previewControls
            x: Math.max(12, (previewControlsFlick.width - implicitWidth) / 2)
            height: previewControlsFlick.height
            spacing: 5
            AppIconButton {
                objectName: "previewPreviousButton"
                iconName: "previous"
                compact: true
                flat: true
                Accessible.name: qsTr("上一帧")
                toolTipText: Accessible.name
                enabled: preview.duration > 0
                onClicked: preview.seek(Math.max(0, preview.position - 1))
            }
            AppIconButton {
                objectName: "previewPlayButton"
                iconName: preview.playing ? "pause" : "play"
                compact: true
                Accessible.name: preview.playing ? qsTr("暂停") : qsTr("播放")
                toolTipText: Accessible.name + qsTr("（空格）")
                primary: true
                enabled: preview.duration > 0
                onClicked: preview.playing || controls.viewport.playbackRequested ? controls.viewport.pause() : controls.viewport.playPreview()
            }
            AppIconButton {
                objectName: "previewStopButton"
                iconName: "stop"
                compact: true
                flat: true
                Accessible.name: qsTr("停止并回到开头")
                toolTipText: Accessible.name
                enabled: preview.duration > 0
                onClicked: controls.viewport.stopPreview()
            }
            Text {
                text: preview.position + " / " + preview.duration
                color: Theme.textMuted
                font.family: Theme.monoFontFamily
                font.pixelSize: Theme.fontSizeCaption
            }
            AppSlider {
                objectName: "previewPositionSlider"
                compact: true
                Layout.preferredWidth: 128
                from: 0
                to: Math.max(0, preview.duration - 1)
                value: preview.position
                enabled: preview.duration > 0
                onPressedChanged: pressed ? controls.viewport.beginScrub() : controls.viewport.endScrub()
                onMoved: controls.viewport.seek(Math.round(value))
                Accessible.name: qsTr("播放位置")
            }
            AppComboBox {
                Layout.preferredWidth: 70
                textRole: "label"
                valueRole: "value"
                model: [
                    {
                        label: "1×",
                        value: 1.0
                    },
                    {
                        label: "1.25×",
                        value: 1.25
                    },
                    {
                        label: "1.5×",
                        value: 1.5
                    },
                    {
                        label: "1.75×",
                        value: 1.75
                    },
                    {
                        label: "2×",
                        value: 2.0
                    },
                    {
                        label: "2.5×",
                        value: 2.5
                    },
                    {
                        label: "3×",
                        value: 3.0
                    }
                ]
                onActivated: preview.playbackRate = Number(currentValue)
                Accessible.name: qsTr("播放速度")
            }
            AppIconButton {
                objectName: "previewMuteButton"
                iconName: controls.viewport.previewMuted || controls.viewport.previewVolume <= 0 ? "mute" : "volume"
                compact: true
                flat: true
                Accessible.name: controls.viewport.previewMuted ? qsTr("取消静音") : qsTr("静音")
                toolTipText: Accessible.name
                onClicked: controls.viewport.toggleMute()
            }
            AppSlider {
                compact: true
                Layout.preferredWidth: 72
                from: 0
                to: 1
                stepSize: 0.01
                value: controls.viewport.previewMuted ? 0 : controls.viewport.previewVolume
                onMoved: {
                    controls.viewport.previewVolume = value;
                    controls.viewport.previewMuted = value <= 0;
                    if (value > 0)
                        controls.viewport.previewVolumeBeforeMute = value;
                }
                Accessible.name: qsTr("预览音量")
            }
            Text {
                visible: preview.droppedFrames > 0
                text: qsTr("掉帧 ") + preview.droppedFrames
                color: Theme.warning
                font.pixelSize: Theme.fontSizeCaption
            }
            AppIconButton {
                objectName: "previewZoomOutButton"
                iconName: "zoom-out"
                compact: true
                flat: true
                Accessible.name: qsTr("缩小预览")
                toolTipText: Accessible.name
                onClicked: controls.viewport.viewportZoom = Math.max(0.5, controls.viewport.viewportZoom / 1.2)
            }
            AppButton {
                objectName: "previewZoomReset"
                text: Math.round(controls.viewport.viewportZoom * 100) + "%"
                compact: true
                quiet: true
                Accessible.name: qsTr("重置预览视图")
                ToolTip.visible: hovered
                ToolTip.text: Accessible.name
                onClicked: controls.viewport.resetViewport()
            }
            AppIconButton {
                objectName: "previewZoomInButton"
                iconName: "zoom-in"
                compact: true
                flat: true
                Accessible.name: qsTr("放大预览")
                toolTipText: Accessible.name
                onClicked: controls.viewport.viewportZoom = Math.min(4, controls.viewport.viewportZoom * 1.2)
            }
            AppIconButton {
                objectName: "previewFullscreenButton"
                iconName: "fullscreen"
                compact: true
                flat: true
                Accessible.name: qsTr("切换全屏")
                toolTipText: Accessible.name + " (F11)"
                onClicked: controls.viewport.toggleFullscreen()
            }
        }
    }
}

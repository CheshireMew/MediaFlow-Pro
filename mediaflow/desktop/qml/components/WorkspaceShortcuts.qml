import QtQuick
import QtQuick.Controls

Item {
    id: shortcuts
    required property Item host
    required property var preview
    required property var timelineView

    Shortcut {
        sequence: "Ctrl+I"
        enabled: shortcuts.host.shortcutsEnabled && shortcuts.host.canEdit
        onActivated: shortcuts.host.openMediaImportDialog()
    }
    Shortcut {
        sequence: "Ctrl+M"
        enabled: shortcuts.host.shortcutsEnabled
        onActivated: shortcuts.host.activeMode = "export"
    }
    Shortcut {
        sequence: "Space"
        enabled: shortcuts.host.shortcutsEnabled
        autoRepeat: false
        onActivated: shortcuts.preview.playing || shortcuts.preview.playbackRequested
            ? shortcuts.preview.pause() : shortcuts.host.playPreview()
    }
    Shortcut {
        sequence: "F11"
        enabled: shortcuts.host.shortcutsEnabled
        onActivated: shortcuts.host.toggleFullscreen()
    }
    Shortcut {
        sequence: "J"
        enabled: shortcuts.host.shortcutsEnabled
        onActivated: {
            shortcuts.preview.playbackRate = shortcuts.preview.playing
                    && shortcuts.preview.playbackRate < 0
                ? Math.max(-4, shortcuts.preview.playbackRate * 2) : -1.0;
            shortcuts.host.playReversePreview();
        }
    }
    Shortcut {
        sequence: "K"
        enabled: shortcuts.host.shortcutsEnabled
        onActivated: shortcuts.preview.pause()
    }
    Shortcut {
        sequence: "L"
        enabled: shortcuts.host.shortcutsEnabled
        onActivated: {
            shortcuts.preview.playbackRate = shortcuts.preview.playing
                    && shortcuts.preview.playbackRate > 0
                ? Math.min(4, shortcuts.preview.playbackRate * 2) : 1.0;
            shortcuts.host.playPreview();
        }
    }
    Shortcut {
        sequence: "S"
        enabled: shortcuts.host.shortcutsEnabled
        onActivated: shortcuts.timelineView.snapEnabled = !shortcuts.timelineView.snapEnabled
    }
    Shortcut {
        sequence: "Ctrl+K"
        enabled: shortcuts.host.shortcutsEnabled && shortcuts.host.canEdit
            && timelineController.selectedClipId.length > 0
        onActivated: timelineController.splitClip(
            timelineController.selectedClipId, shortcuts.preview.position)
    }
    Shortcut {
        sequence: "Ctrl+B"
        enabled: shortcuts.host.shortcutsEnabled && shortcuts.host.canEdit
            && timelineController.selectedClipId.length > 0
        onActivated: timelineController.splitClip(
            timelineController.selectedClipId, shortcuts.preview.position)
    }
    Shortcut {
        sequence: "Delete"
        enabled: shortcuts.host.shortcutsEnabled && shortcuts.host.canEdit
            && timelineController.selectedClipIds.length > 0
        onActivated: timelineController.deleteSelectedClips(false)
    }
    Shortcut {
        sequence: "Shift+Delete"
        enabled: shortcuts.host.shortcutsEnabled && shortcuts.host.canEdit
            && timelineController.selectedClipIds.length > 0
        onActivated: timelineController.deleteSelectedClips(true)
    }
    Shortcut {
        sequence: "Ctrl+Z"
        enabled: shortcuts.host.shortcutsEnabled && shortcuts.host.canEdit
            && timelineController.canUndo
        onActivated: timelineController.undo()
    }
    Shortcut {
        sequences: ["Ctrl+Y", "Ctrl+Shift+Z"]
        enabled: shortcuts.host.shortcutsEnabled && shortcuts.host.canEdit
            && timelineController.canRedo
        onActivated: timelineController.redo()
    }
    Shortcut {
        sequence: "Ctrl+D"
        enabled: shortcuts.host.shortcutsEnabled && shortcuts.host.canEdit
            && timelineController.selectedClipId.length > 0
        onActivated: timelineController.duplicateClip(
            timelineController.selectedClipId,
            shortcuts.timelineView.pixelsPerFrame,
            shortcuts.preview.position)
    }
    Shortcut {
        sequence: "Ctrl+A"
        enabled: shortcuts.host.shortcutsEnabled
            && timelineController.clipsModel.rowCount() > 0
        onActivated: timelineController.selectAllClips()
    }
    Shortcut {
        sequence: "Ctrl+Shift+A"
        enabled: shortcuts.host.shortcutsEnabled
        onActivated: shortcuts.timelineView.clearTimelineSelection()
    }
    Shortcut {
        sequence: "Escape"
        enabled: shortcuts.host.shortcutsEnabled
            && timelineController.selectedClipIds.length > 0
        onActivated: shortcuts.timelineView.clearTimelineSelection()
    }
    Shortcut {
        sequence: "I"
        enabled: shortcuts.host.shortcutsEnabled && shortcuts.host.canEdit
            && workspaceController.timelineDurationFrames > 0
        onActivated: timelineController.setSequenceInPoint(shortcuts.preview.position)
    }
    Shortcut {
        sequence: "O"
        enabled: shortcuts.host.shortcutsEnabled && shortcuts.host.canEdit
            && workspaceController.timelineDurationFrames > 0
        onActivated: timelineController.setSequenceOutPoint(shortcuts.preview.position)
    }
    Shortcut {
        sequence: "Ctrl+Shift+X"
        enabled: shortcuts.host.shortcutsEnabled && shortcuts.host.canEdit
            && workspaceController.hasSequenceInOut
        onActivated: timelineController.clearSequenceInOut()
    }
    Shortcut {
        sequence: "Left"
        enabled: shortcuts.host.shortcutsEnabled
        onActivated: shortcuts.preview.seek(shortcuts.preview.position - 1)
    }
    Shortcut {
        sequence: "Right"
        enabled: shortcuts.host.shortcutsEnabled
        onActivated: shortcuts.preview.seek(shortcuts.preview.position + 1)
    }
    Shortcut {
        sequence: "Home"
        enabled: shortcuts.host.shortcutsEnabled
        onActivated: shortcuts.preview.seek(0)
    }
    Shortcut {
        sequence: "End"
        enabled: shortcuts.host.shortcutsEnabled
            && workspaceController.timelineDurationFrames > 0
        onActivated: shortcuts.preview.seek(workspaceController.timelineDurationFrames - 1)
    }
    Shortcut {
        sequence: "\\"
        enabled: shortcuts.host.shortcutsEnabled
        onActivated: shortcuts.timelineView.fitTimeline()
    }
    Shortcut {
        sequence: "Ctrl+S"
        enabled: shortcuts.host.shortcutsEnabled && shortcuts.host.canEdit
        onActivated: workspaceController.saveProject()
    }
    Shortcut {
        sequence: "M"
        enabled: shortcuts.host.shortcutsEnabled && shortcuts.host.canEdit
        onActivated: timelineController.addTimelineMarker(shortcuts.preview.position)
    }
}

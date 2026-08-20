import QtQuick
import QtQuick.Controls

Item {
    id: shortcuts
    required property Item host
    required property var preview
    required property var timelineView

    Shortcut {
        sequence: "Ctrl+Alt+1"
        enabled: shortcuts.host.shortcutsEnabled
        onActivated: shortcuts.host.setWorkspaceLayoutPreset("standard")
    }
    Shortcut {
        sequence: "Ctrl+Alt+2"
        enabled: shortcuts.host.shortcutsEnabled
        onActivated: shortcuts.host.setWorkspaceLayoutPreset("media")
    }
    Shortcut {
        sequence: "Ctrl+Alt+3"
        enabled: shortcuts.host.shortcutsEnabled
        onActivated: shortcuts.host.setWorkspaceLayoutPreset("vertical")
    }
    Shortcut {
        sequence: "Ctrl+Alt+0"
        enabled: shortcuts.host.shortcutsEnabled
            && shortcuts.host.maximizedPanel.length > 0
        onActivated: shortcuts.host.maximizedPanel = ""
    }
    Shortcut {
        sequence: "Ctrl+Alt+P"
        enabled: shortcuts.host.shortcutsEnabled
        onActivated: shortcuts.host.togglePanelMaximized("preview")
    }
    Shortcut {
        sequence: "Ctrl+Alt+T"
        enabled: shortcuts.host.shortcutsEnabled
        onActivated: shortcuts.host.togglePanelMaximized("timeline")
    }

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
            && mediaflow.timelineViewController.selectedClipId.length > 0
        onActivated: mediaflow.timelineClipController.splitClip(
            mediaflow.timelineViewController.selectedClipId, shortcuts.preview.position)
    }
    Shortcut {
        sequence: "Ctrl+B"
        enabled: shortcuts.host.shortcutsEnabled && shortcuts.host.canEdit
            && mediaflow.timelineViewController.selectedClipId.length > 0
        onActivated: mediaflow.timelineClipController.splitClip(
            mediaflow.timelineViewController.selectedClipId, shortcuts.preview.position)
    }
    Shortcut {
        sequence: "Delete"
        enabled: shortcuts.host.shortcutsEnabled && shortcuts.host.canEdit
            && mediaflow.timelineViewController.selectedClipIds.length > 0
        onActivated: mediaflow.timelineClipController.deleteSelectedClips(false)
    }
    Shortcut {
        sequence: "Shift+Delete"
        enabled: shortcuts.host.shortcutsEnabled && shortcuts.host.canEdit
            && mediaflow.timelineViewController.selectedClipIds.length > 0
        onActivated: mediaflow.timelineClipController.deleteSelectedClips(true)
    }
    Shortcut {
        sequence: "Ctrl+Z"
        enabled: shortcuts.host.shortcutsEnabled && shortcuts.host.canEdit
            && mediaflow.timelineViewController.canUndo
        onActivated: mediaflow.timelineStructureController.undo()
    }
    Shortcut {
        sequences: ["Ctrl+Y", "Ctrl+Shift+Z"]
        enabled: shortcuts.host.shortcutsEnabled && shortcuts.host.canEdit
            && mediaflow.timelineViewController.canRedo
        onActivated: mediaflow.timelineStructureController.redo()
    }
    Shortcut {
        sequence: "Ctrl+D"
        enabled: shortcuts.host.shortcutsEnabled && shortcuts.host.canEdit
            && mediaflow.timelineViewController.selectedClipId.length > 0
        onActivated: mediaflow.timelineClipController.duplicateClip(
            mediaflow.timelineViewController.selectedClipId,
            shortcuts.timelineView.pixelsPerFrame,
            shortcuts.preview.position)
    }
    Shortcut {
        sequence: "Ctrl+A"
        enabled: shortcuts.host.shortcutsEnabled
            && mediaflow.timelineViewController.clipsModel.rowCount() > 0
        onActivated: mediaflow.timelineViewController.selectAllClips()
    }
    Shortcut {
        sequence: "Ctrl+Shift+A"
        enabled: shortcuts.host.shortcutsEnabled
        onActivated: shortcuts.timelineView.clearTimelineSelection()
    }
    Shortcut {
        sequence: "Escape"
        enabled: shortcuts.host.shortcutsEnabled
            && (shortcuts.host.maximizedPanel.length > 0
                || mediaflow.timelineViewController.selectedClipIds.length > 0)
        onActivated: {
            if (shortcuts.host.maximizedPanel.length > 0)
                shortcuts.host.maximizedPanel = "";
            else
                shortcuts.timelineView.clearTimelineSelection();
        }
    }
    Shortcut {
        sequence: "I"
        enabled: shortcuts.host.shortcutsEnabled && shortcuts.host.canEdit
            && mediaflow.workspaceViewController.timelineDurationFrames > 0
        onActivated: mediaflow.timelineStructureController.setSequenceInPoint(shortcuts.preview.position)
    }
    Shortcut {
        sequence: "O"
        enabled: shortcuts.host.shortcutsEnabled && shortcuts.host.canEdit
            && mediaflow.workspaceViewController.timelineDurationFrames > 0
        onActivated: mediaflow.timelineStructureController.setSequenceOutPoint(shortcuts.preview.position)
    }
    Shortcut {
        sequence: "Ctrl+Shift+X"
        enabled: shortcuts.host.shortcutsEnabled && shortcuts.host.canEdit
            && mediaflow.workspaceViewController.hasSequenceInOut
        onActivated: mediaflow.timelineStructureController.clearSequenceInOut()
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
            && mediaflow.workspaceViewController.timelineDurationFrames > 0
        onActivated: shortcuts.preview.seek(mediaflow.workspaceViewController.timelineDurationFrames - 1)
    }
    Shortcut {
        sequence: "\\"
        enabled: shortcuts.host.shortcutsEnabled
        onActivated: shortcuts.timelineView.fitTimeline()
    }
    Shortcut {
        sequence: "Ctrl+S"
        enabled: shortcuts.host.shortcutsEnabled && shortcuts.host.canEdit
        onActivated: mediaflow.workspaceSequenceController.saveProject()
    }
    Shortcut {
        sequence: "M"
        enabled: shortcuts.host.shortcutsEnabled && shortcuts.host.canEdit
        onActivated: mediaflow.timelineStructureController.addTimelineMarker(shortcuts.preview.position)
    }
}

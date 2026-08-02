pragma Singleton
import QtQuick

QtObject {
    readonly property bool highContrast: settingsController.settingsData.theme === "high_contrast"

    function canvasMonospaceFont(pixelSize) {
        return String(pixelSize) + "px " + JSON.stringify(monoFontFamily)
    }

    // Neutral editing surfaces follow the fixed spatial hierarchy of the
    // workspace: global chrome, panes, controls, then floating UI.
    readonly property color transparent: "#00000000"
    readonly property color window: highContrast ? "#000000" : "#17181a"
    readonly property color surfaceSunken: highContrast ? "#000000" : "#1b1c1f"
    readonly property color surface: highContrast ? "#080808" : "#222326"
    readonly property color surfaceRaised: highContrast ? "#111111" : "#2a2b2f"
    readonly property color surfaceFloating: highContrast ? "#1a1a1a" : "#303136"
    readonly property color surfaceHover: highContrast ? "#292929" : "#36373b"
    readonly property color surfacePressed: highContrast ? "#3a3a3a" : "#404146"
    readonly property color surfaceDisabled: highContrast ? "#0a0a0a" : "#242529"
    readonly property color borderSubtle: highContrast ? "#8a8a8a" : "#2b2c30"
    readonly property color border: highContrast ? "#c8c8c8" : "#37383d"
    readonly property color borderStrong: highContrast ? "#ffffff" : "#4a4c52"
    readonly property color divider: borderSubtle

    // Text hierarchy.
    readonly property color textStrong: "#ffffff"
    readonly property color text: highContrast ? "#ffffff" : "#eeeeef"
    readonly property color textSubtle: highContrast ? "#f0f0f0" : "#b7b9bd"
    readonly property color textMuted: highContrast ? "#dedede" : "#8e9197"
    readonly property color textDisabled: highContrast ? "#a8a8a8" : "#62656b"
    readonly property color onAccent: highContrast ? "#ffffff" : "#ffffff"

    // Cyan is the single interactive signal. It is not used as decoration.
    readonly property color accent: highContrast ? "#00f1ff" : "#20c7d4"
    readonly property color accentHover: highContrast ? "#8af8ff" : "#51dbe4"
    readonly property color accentPressed: highContrast ? "#00bac5" : "#12a9b5"
    readonly property color accentSoft: highContrast ? "#00464b" : "#17383c"
    readonly property color selectionSoft: highContrast ? "#005a61" : "#1a474c"
    readonly property color focusColor: accent
    readonly property color cut: highContrast ? "#ffffff" : "#f0f1f2"
    readonly property color cutHover: "#ffffff"
    readonly property color cutSoft: highContrast ? "#444444" : "#34363a"

    // Status colors have dedicated soft surfaces so they never borrow brand
    // meaning.
    readonly property color success: highContrast ? "#7dffb1" : "#68d391"
    readonly property color successSoft: highContrast ? "#064c26" : "#183326"
    readonly property color warning: highContrast ? "#ffd56b" : "#e7b96a"
    readonly property color warningSoft: highContrast ? "#5a3d00" : "#392e1d"
    readonly property color danger: highContrast ? "#ff8c98" : "#ef6d7a"
    readonly property color dangerHover: highContrast ? "#ffc5cb" : "#ff8c98"
    readonly property color dangerPressed: highContrast ? "#ff6574" : "#d95665"
    readonly property color dangerSoft: highContrast ? "#5e111a" : "#3b2028"

    // Media categories.
    readonly property color video: highContrast ? "#5ff5ff" : "#29bec9"
    readonly property color videoSoft: highContrast ? "#07575d" : "#155057"
    readonly property color audio: highContrast ? "#8efaff" : "#55cbd3"
    readonly property color audioSoft: highContrast ? "#07575d" : "#19464b"
    readonly property color subtitle: highContrast ? "#ffd58b" : "#e4ad63"
    readonly property color subtitleSoft: highContrast ? "#674000" : "#412f1e"
    readonly property color web: highContrast ? "#ffadd8" : "#dc82b4"
    readonly property color webSoft: highContrast ? "#6a1745" : "#40263a"
    readonly property color image: highContrast ? "#a3eca9" : "#78c28c"
    readonly property color imageSoft: highContrast ? "#235d29" : "#223b2b"
    readonly property color transcriptTrack: highContrast ? "#00f1ff" : "#20c7d4"
    readonly property color transcriptTrackHover: highContrast ? "#8af8ff" : "#51dbe4"
    readonly property color transcriptTrackSoft: highContrast ? "#00464b" : "#17383c"
    readonly property color compound: highContrast ? "#8efaff" : "#64cbd2"
    readonly property color compoundSoft: highContrast ? "#07575d" : "#23454a"
    readonly property color transition: highContrast ? "#8efaff" : "#6dc9d0"
    readonly property color transitionSoft: highContrast ? "#07575d" : "#244348"
    readonly property color marker: warning
    readonly property color markerSoft: warningSoft
    readonly property color waveform: highContrast ? "#ffffff" : "#c4c5cf"
    readonly property color waveformMuted: highContrast ? "#b5b5b5" : "#626572"

    // Overlays, preview and timeline.
    readonly property color overlay: highContrast ? "#e6000000" : "#c40a0a0f"
    readonly property color shadow: highContrast ? "#ff000000" : "#b8000000"
    readonly property color dragOverlay: highContrast ? "#b300464b" : "#7017383c"
    readonly property color previewBackdrop: "#000000"
    readonly property color previewSurface: highContrast ? "#080808" : "#090a0e"
    readonly property color timelineBackground: highContrast ? "#000000" : "#202124"
    readonly property color timelineTrackA: highContrast ? "#101010" : "#252629"
    readonly property color timelineTrackB: highContrast ? "#1b1b1b" : "#292a2d"
    readonly property color timelineRuler: highContrast ? "#0c0c0c" : "#26272a"
    readonly property color timelineGrid: highContrast ? "#8b8b8b" : "#35363a"
    readonly property color timelineGridStrong: highContrast ? "#ffffff" : "#4b4d52"
    readonly property color timelineLabel: highContrast ? "#ffffff" : "#a2a4a9"
    readonly property real timelineRangeFillOpacity: highContrast ? 0.22 : 0.14
    readonly property real timelineRangeBorderOpacity: highContrast ? 0.72 : 0.58

    // Control-specific aliases keep component styling semantic and prevent
    // business pages from choosing colors.
    readonly property color control: surfaceRaised
    readonly property color controlHover: surfaceHover
    readonly property color controlPressed: surfacePressed
    readonly property color controlDisabled: surfaceDisabled
    readonly property color field: surfaceSunken
    readonly property color fieldHover: surface
    readonly property color fieldReadOnly: surface
    readonly property color popup: surfaceFloating
    readonly property color dialog: surfaceFloating
    readonly property color progressTrack: highContrast ? "#2e2e2e" : "#414247"
    readonly property color progressFill: accent

    readonly property int radiusSmall: 7
    readonly property int radius: 10
    readonly property int radiusLarge: 14
    readonly property int spacing: 12
    readonly property int workspaceOuterGutter: 10
    readonly property int workspacePanelGap: 10
    readonly property int workspaceNavigationHeight: 68
    readonly property int workspacePanelHeaderHeight: 50
    readonly property int workspaceToolMinimumWidth: 340
    readonly property int workspaceInspectorMinimumWidth: 300
    readonly property int workspacePreviewMinimumWidth: 360
    readonly property int workspaceTimelineMinimumHeight: 210
    readonly property int controlHeightCompact: 30
    readonly property int controlHeight: 36
    readonly property int controlHeightLarge: 42
    readonly property int iconSizeSmall: 14
    readonly property int iconSizeToolbar: 16
    readonly property int iconButtonSize: 32
    readonly property real iconStrokeWidth: 1.45
    readonly property int dialogPadding: 18
    readonly property int durationFast: 110
    readonly property int duration: 170
    readonly property int durationSlow: 240
    readonly property int durationProgress: 1100
    readonly property int fontSizeCaption: 11
    readonly property int fontSizeBodySmall: 12
    readonly property int fontSizeBody: 13
    readonly property int fontSizeBodyLarge: 14
    readonly property int fontSizeTitleSmall: 15
    readonly property int fontSizeSection: 16
    readonly property int fontSizeTitle: 18
    readonly property int fontSizeDisplay: 26
    readonly property int fontSizeHero: 38
    readonly property string monoFontFamily: applicationMonospaceFontFamily
}

pragma Singleton
import QtQuick

QtObject {
    readonly property bool highContrast: settingsController.settingsData.theme === "high_contrast"

    function canvasMonospaceFont(pixelSize) {
        return String(pixelSize) + "px " + JSON.stringify(monoFontFamily)
    }

    // Graphite workbench surfaces. The hierarchy comes from tone before rules:
    // borders stay quiet until a control is focused or selected.
    readonly property color transparent: "#00000000"
    readonly property color window: highContrast ? "#000000" : "#101116"
    readonly property color surfaceSunken: highContrast ? "#000000" : "#12141a"
    readonly property color surface: highContrast ? "#080808" : "#15171d"
    readonly property color surfaceRaised: highContrast ? "#111111" : "#1a1c23"
    readonly property color surfaceFloating: highContrast ? "#1a1a1a" : "#20222b"
    readonly property color surfaceHover: highContrast ? "#292929" : "#272a34"
    readonly property color surfacePressed: highContrast ? "#3a3a3a" : "#30333f"
    readonly property color surfaceDisabled: highContrast ? "#0a0a0a" : "#17191f"
    readonly property color borderSubtle: highContrast ? "#8a8a8a" : "#20222a"
    readonly property color border: highContrast ? "#c8c8c8" : "#2c2f38"
    readonly property color borderStrong: highContrast ? "#ffffff" : "#41444f"
    readonly property color divider: borderSubtle

    // Text hierarchy.
    readonly property color textStrong: "#ffffff"
    readonly property color text: highContrast ? "#ffffff" : "#ececf1"
    readonly property color textSubtle: highContrast ? "#f0f0f0" : "#b2b4be"
    readonly property color textMuted: highContrast ? "#dedede" : "#838691"
    readonly property color textDisabled: highContrast ? "#a8a8a8" : "#5c5f69"
    readonly property color onAccent: highContrast ? "#ffffff" : "#ffffff"

    // Periwinkle identifies selection and primary action. Coral is reserved for
    // edit points, playheads and destructive timeline intent.
    readonly property color accent: highContrast ? "#b5aaff" : "#9488ef"
    readonly property color accentHover: highContrast ? "#ddd7ff" : "#b1a8f6"
    readonly property color accentPressed: highContrast ? "#8d7cff" : "#7869dc"
    readonly property color accentSoft: highContrast ? "#352a79" : "#25213b"
    readonly property color selectionSoft: highContrast ? "#463797" : "#30294d"
    readonly property color focusColor: accent
    readonly property color cut: highContrast ? "#ff9caa" : "#ff6f7d"
    readonly property color cutHover: highContrast ? "#ffd1d8" : "#ff96a1"
    readonly property color cutSoft: highContrast ? "#661b2a" : "#3d202a"

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
    readonly property color video: highContrast ? "#8db7ff" : "#6f94e8"
    readonly property color videoSoft: highContrast ? "#193f7a" : "#202d49"
    readonly property color audio: highContrast ? "#d3a7ff" : "#b487e6"
    readonly property color audioSoft: highContrast ? "#512a7e" : "#38284c"
    readonly property color subtitle: highContrast ? "#ffd58b" : "#e4ad63"
    readonly property color subtitleSoft: highContrast ? "#674000" : "#412f1e"
    readonly property color web: highContrast ? "#ffadd8" : "#dc82b4"
    readonly property color webSoft: highContrast ? "#6a1745" : "#40263a"
    readonly property color image: highContrast ? "#a3eca9" : "#78c28c"
    readonly property color imageSoft: highContrast ? "#235d29" : "#223b2b"
    readonly property color transcriptTrack: highContrast ? "#b5aaff" : "#9a8cf7"
    readonly property color transcriptTrackHover: highContrast ? "#ddd7ff" : "#b8aefb"
    readonly property color transcriptTrackSoft: highContrast ? "#352a79" : "#292440"
    readonly property color compound: highContrast ? "#dcc0ff" : "#b7a0dc"
    readonly property color compoundSoft: highContrast ? "#4d276f" : "#342b45"
    readonly property color transition: highContrast ? "#9be8ff" : "#73b7d2"
    readonly property color transitionSoft: highContrast ? "#164f5e" : "#213842"
    readonly property color marker: warning
    readonly property color markerSoft: warningSoft
    readonly property color waveform: highContrast ? "#ffffff" : "#c4c5cf"
    readonly property color waveformMuted: highContrast ? "#b5b5b5" : "#626572"

    // Overlays, preview and timeline.
    readonly property color overlay: highContrast ? "#e6000000" : "#c40a0a0f"
    readonly property color shadow: highContrast ? "#ff000000" : "#b8000000"
    readonly property color dragOverlay: highContrast ? "#b3352a79" : "#7025213b"
    readonly property color previewBackdrop: "#000000"
    readonly property color previewSurface: highContrast ? "#080808" : "#090a0e"
    readonly property color timelineBackground: highContrast ? "#000000" : "#111319"
    readonly property color timelineTrackA: highContrast ? "#101010" : "#15171e"
    readonly property color timelineTrackB: highContrast ? "#1b1b1b" : "#181a22"
    readonly property color timelineRuler: highContrast ? "#0c0c0c" : "#171920"
    readonly property color timelineGrid: highContrast ? "#8b8b8b" : "#292c35"
    readonly property color timelineGridStrong: highContrast ? "#ffffff" : "#393c46"
    readonly property color timelineLabel: highContrast ? "#ffffff" : "#9294a1"
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
    readonly property color progressTrack: highContrast ? "#2e2e2e" : "#2a2c37"
    readonly property color progressFill: accent

    readonly property int radiusSmall: 7
    readonly property int radius: 10
    readonly property int radiusLarge: 14
    readonly property int spacing: 12
    readonly property int controlHeightCompact: 30
    readonly property int controlHeight: 36
    readonly property int controlHeightLarge: 42
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

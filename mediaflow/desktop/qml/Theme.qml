pragma Singleton
import QtQuick

QtObject {
    readonly property bool highContrast: settingsController.settingsData.theme === "high_contrast"
    readonly property color window: highContrast ? "#000000" : "#0b0d10"
    readonly property color surface: highContrast ? "#080808" : "#12151a"
    readonly property color surfaceRaised: highContrast ? "#101010" : "#181c22"
    readonly property color surfaceFloating: highContrast ? "#181818" : "#1d222a"
    readonly property color surfaceSunken: highContrast ? "#050505" : "#0e1116"
    readonly property color surfaceHover: highContrast ? "#242424" : "#202631"
    readonly property color border: highContrast ? "#a0a0a0" : "#2a303a"
    readonly property color borderStrong: highContrast ? "#ffffff" : "#3b4552"
    readonly property color text: "#ffffff"
    readonly property color textMuted: highContrast ? "#e0e0e0" : "#929cac"
    readonly property color accent: highContrast ? "#007cff" : "#2389f4"
    readonly property color accentHover: highContrast ? "#72b8ff" : "#45a1ff"
    readonly property color accentSoft: highContrast ? "#003d7a" : "#163657"
    readonly property color success: "#39c986"
    readonly property color warning: "#f0ad4e"
    readonly property color danger: "#ef6262"
    readonly property color video: "#3478c8"
    readonly property color audio: "#7e5ac7"
    readonly property color subtitle: "#be7a37"
    readonly property color transcriptTrack: highContrast ? "#00ffd0" : "#35d0b5"
    readonly property color transcriptTrackHover: highContrast ? "#7fffe8" : "#52e3c9"
    readonly property color transcriptTrackSoft: highContrast ? "#004b40" : "#123c39"
    readonly property int radiusSmall: 6
    readonly property int radius: 10
    readonly property int radiusLarge: 16
    readonly property int spacing: 12
    readonly property int fontSizeCaption: 11
    readonly property int fontSizeBodySmall: 12
    readonly property int fontSizeBody: 13
    readonly property int fontSizeBodyLarge: 14
    readonly property int fontSizeTitleSmall: 15
    readonly property int fontSizeSection: 16
    readonly property int fontSizeTitle: 18
    readonly property int fontSizeDisplay: 26
    readonly property int fontSizeHero: 38
    readonly property string monoFontFamily: "Consolas"
}

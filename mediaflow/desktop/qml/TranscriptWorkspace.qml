import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "."
import "components"

ColumnLayout {
    id: root
    objectName: "transcriptWorkspace"
    property int playheadFrame: 0
    property bool playbackActive: false
    signal importRequested
    signal seekRequested(int frame)

    spacing: 9

    TabBar {
        id: transcriptTabs
        objectName: "transcriptWorkspaceTabs"
        Layout.fillWidth: true
        TabButton { objectName: "transcriptSection_transcribe"; text: qsTr("转写") }
        TabButton { objectName: "transcriptSection_subtitle"; text: qsTr("字幕") }
        TabButton { objectName: "transcriptSection_translate"; text: qsTr("翻译") }
        TabButton { objectName: "transcriptSection_glossary"; text: qsTr("术语表") }
    }

    StackLayout {
        Layout.fillWidth: true
        Layout.fillHeight: true
        currentIndex: Math.min(transcriptTabs.currentIndex, 2)

        TranscriptPanel {
            onModeRequested: function (mode) {
                if (mode === "subtitle")
                    transcriptTabs.currentIndex = 1;
                else if (mode === "translate")
                    transcriptTabs.currentIndex = 2;
            }
        }
        SubtitlePanel {
            playheadFrame: root.playheadFrame
            playbackActive: root.playbackActive
            onModeRequested: function (mode) {
                if (mode === "transcript")
                    transcriptTabs.currentIndex = 0;
                else if (mode === "translate")
                    transcriptTabs.currentIndex = 2;
            }
            onImportRequested: root.importRequested()
            onSeekRequested: function (frame) { root.seekRequested(frame); }
        }
        TranslationPanel {
            objectName: "translationSectionPanel"
            sectionIndex: Math.max(0, transcriptTabs.currentIndex - 2)
            showSectionTabs: false
            onModeRequested: function (mode) {
                if (mode === "transcript")
                    transcriptTabs.currentIndex = 0;
            }
            onImportRequested: root.importRequested()
        }
    }
}

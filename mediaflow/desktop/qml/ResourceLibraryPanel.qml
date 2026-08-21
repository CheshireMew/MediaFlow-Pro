import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtMultimedia
import "."
import "components"

Item {
    id: root
    objectName: "resourceLibraryPanel"
    property int playheadFrame: 0
    property real pixelsPerFrame: 3.0
    property bool snapEnabled: true
    property string selectedCategory: ""
    property string selectedCollection: ""
    property string activeAudioResourceKey: ""
    readonly property bool canEdit:
        mediaflow.workspaceViewController.actionCapabilities.canEdit

    function refresh() {
        mediaflow.resourceLibraryController.refresh(
            root.selectedCategory,
            searchField.text,
            root.selectedCollection);
    }

    function toggleAudioPreview(resourceKey, previewUrl) {
        if (root.activeAudioResourceKey === resourceKey
                && previewPlayer.playbackState === MediaPlayer.PlayingState) {
            previewPlayer.stop();
            root.activeAudioResourceKey = "";
            return;
        }
        previewPlayer.stop();
        previewPlayer.source = previewUrl;
        root.activeAudioResourceKey = resourceKey;
        previewPlayer.play();
    }

    Component.onCompleted: root.refresh()

    Connections {
        target: mediaflow.workspaceViewController
        function onProjectStateChanged() { root.refresh(); }
    }

    Timer {
        id: searchTimer
        interval: 180
        repeat: false
        onTriggered: root.refresh()
    }

    AudioOutput {
        id: previewAudioOutput
        volume: 0.8
    }

    MediaPlayer {
        id: previewPlayer
        audioOutput: previewAudioOutput
        onMediaStatusChanged: {
            if (mediaStatus === MediaPlayer.EndOfMedia
                    || mediaStatus === MediaPlayer.InvalidMedia)
                root.activeAudioResourceKey = "";
        }
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: 10

        RowLayout {
            Layout.fillWidth: true
            spacing: 8
            Text {
                text: qsTr("资源库")
                color: Theme.textStrong
                font.pixelSize: Theme.fontSizeTitle
                font.weight: Font.DemiBold
            }
            Item { Layout.fillWidth: true }
            Text {
                text: qsTr("%1 项").arg(mediaflow.resourceLibraryController.resultCount)
                color: Theme.textMuted
                font.pixelSize: Theme.fontSizeSmall
            }
        }

        AppTextField {
            id: searchField
            objectName: "resourceLibrarySearchField"
            Layout.fillWidth: true
            placeholderText: qsTr("搜索名称、标签、用途或提供方")
            onTextChanged: searchTimer.restart()
        }

        Flow {
            Layout.fillWidth: true
            spacing: 6
            Repeater {
                model: mediaflow.resourceLibraryController.categoryOptions
                AppButton {
                    required property var modelData
                    text: String(modelData.label)
                    compact: true
                    checkable: true
                    checked: root.selectedCategory === String(modelData.value)
                    onClicked: {
                        root.selectedCategory = String(modelData.value);
                        root.refresh();
                    }
                }
            }
        }

        Flow {
            Layout.fillWidth: true
            spacing: 6
            AppButton {
                text: qsTr("发现")
                compact: true
                checkable: true
                checked: root.selectedCollection === ""
                onClicked: {
                    root.selectedCollection = "";
                    root.refresh();
                }
            }
            Repeater {
                model: mediaflow.resourceLibraryController.collectionOptions
                AppButton {
                    required property var modelData
                    text: String(modelData.label)
                    compact: true
                    checkable: true
                    checked: root.selectedCollection === String(modelData.value)
                    onClicked: {
                        root.selectedCollection = String(modelData.value);
                        root.refresh();
                    }
                }
            }
        }

        Rectangle {
            Layout.fillWidth: true
            visible: mediaflow.resourceLibraryController.sourceErrors.length > 0
            implicitHeight: sourceErrorText.implicitHeight + 16
            radius: Theme.radiusSmall
            color: Theme.dangerSoft
            border.color: Theme.danger
            Text {
                id: sourceErrorText
                anchors.fill: parent
                anchors.margins: 8
                text: mediaflow.resourceLibraryController.sourceErrors.join("\n")
                color: Theme.danger
                wrapMode: Text.WordWrap
                font.pixelSize: Theme.fontSizeSmall
            }
        }

        ListView {
            id: resourceList
            objectName: "resourceLibraryList"
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            spacing: 8
            model: mediaflow.resourceLibraryController.resourcesModel
            ScrollBar.vertical: AppScrollBar {}

            delegate: Rectangle {
                id: card
                required property string resourceKey
                required property string category
                required property string name
                required property string description
                required property string provider
                required property var tags
                required property string previewType
                required property string previewUrl
                required property string license
                required property string adoptionType
                required property string adoptionTarget
                required property string presetId
                required property int defaultDurationFrames
                required property int featuredRank
                required property bool isFavorite
                required property bool canAdopt
                width: ListView.view ? ListView.view.width : 0
                height: 148
                radius: Theme.radius
                color: Theme.surfaceRaised
                border.color: Theme.borderSubtle

                RowLayout {
                    anchors.fill: parent
                    anchors.margins: 10
                    spacing: 10

                    Rectangle {
                        Layout.preferredWidth: 112
                        Layout.fillHeight: true
                        radius: Theme.radiusSmall
                        color: Theme.field
                        clip: true
                        Image {
                            anchors.fill: parent
                            source: card.previewType === "image" ? card.previewUrl : ""
                            fillMode: Image.PreserveAspectCrop
                            visible: source.toString().length > 0
                        }
                        Text {
                            anchors.centerIn: parent
                            visible: card.previewType !== "image" || card.previewUrl.length === 0
                            text: card.category === "motion-graphic" ? "MG"
                                : card.category === "sound-effect" ? qsTr("音效")
                                : card.category === "audio-effect" ? qsTr("音频效果")
                                : card.category === "visual-effect" ? qsTr("特效")
                                : card.category === "transition" ? qsTr("转场")
                                : card.category === "zoom" ? qsTr("缩放")
                                : card.category.toUpperCase()
                            color: Theme.textMuted
                            font.pixelSize: Theme.fontSizeBody
                            font.weight: Font.DemiBold
                        }
                    }

                    ColumnLayout {
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        spacing: 4
                        RowLayout {
                            Layout.fillWidth: true
                            Text {
                                Layout.fillWidth: true
                                text: card.name
                                    + (card.featuredRank >= 0 ? qsTr(" · 热门") : "")
                                color: Theme.textStrong
                                font.pixelSize: Theme.fontSizeBody
                                font.weight: Font.DemiBold
                                elide: Text.ElideRight
                            }
                            AppButton {
                                objectName: "resourceFavoriteButton"
                                compact: true
                                text: card.isFavorite ? "★" : "☆"
                                Accessible.name: card.isFavorite
                                    ? qsTr("取消收藏") : qsTr("收藏")
                                onClicked: mediaflow.resourceLibraryController.toggleFavorite(
                                    card.resourceKey)
                            }
                        }
                        Text {
                            Layout.fillWidth: true
                            text: card.description
                            color: Theme.textMuted
                            font.pixelSize: Theme.fontSizeSmall
                            wrapMode: Text.WordWrap
                            maximumLineCount: 2
                            elide: Text.ElideRight
                        }
                        Text {
                            Layout.fillWidth: true
                            text: card.provider + (card.tags.length ? " · " + card.tags.join(" / ") : "")
                            color: Theme.textDisabled
                            font.pixelSize: Theme.fontSizeSmall
                            elide: Text.ElideRight
                        }
                        Item { Layout.fillHeight: true }
                        RowLayout {
                            Layout.fillWidth: true
                            Text {
                                Layout.fillWidth: true
                                text: card.license
                                color: Theme.textDisabled
                                font.pixelSize: Theme.fontSizeSmall
                                elide: Text.ElideRight
                            }
                            AppButton {
                                objectName: "resourceAudioPreviewButton"
                                visible: card.previewType === "audio"
                                    && card.previewUrl.length > 0
                                compact: true
                                text: root.activeAudioResourceKey === card.resourceKey
                                        && previewPlayer.playbackState === MediaPlayer.PlayingState
                                    ? qsTr("停止") : qsTr("试听")
                                onClicked: root.toggleAudioPreview(
                                    card.resourceKey,
                                    card.previewUrl)
                            }
                            AppButton {
                                objectName: "resourceAdoptButton"
                                compact: true
                                primary: true
                                text: card.adoptionType === "editor-preset"
                                    ? qsTr("应用") : qsTr("导入并添加")
                                enabled: root.canEdit && card.canAdopt
                                onClicked: mediaflow.resourceLibraryController.adoptResource(
                                    card.resourceKey,
                                    root.playheadFrame,
                                    root.pixelsPerFrame,
                                    root.snapEnabled)
                            }
                        }
                    }
                }

                HoverHandler {
                    enabled: card.adoptionTarget === "transition"
                        && root.canEdit
                        && mediaflow.timelineViewController.selectedClipId.length > 0
                    onHoveredChanged: {
                        if (hovered) {
                            mediaflow.timelineViewController.previewTransitionAfter(
                                mediaflow.timelineViewController.selectedClipId,
                                card.presetId,
                                card.defaultDurationFrames);
                        } else {
                            mediaflow.timelineViewController.clearTransitionPreview();
                        }
                    }
                }
            }

            Text {
                anchors.centerIn: parent
                visible: resourceList.count === 0
                text: qsTr("没有匹配的资源")
                color: Theme.textMuted
            }
        }
    }
}

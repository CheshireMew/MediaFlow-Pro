import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs
import QtQuick.Layouts
import QtQuick.Window
import "."
import "components"

ApplicationWindow {
    id: window
    width: 1600
    height: 980
    minimumWidth: Screen.desktopAvailableWidth > 0
        ? Math.min(1180, Screen.desktopAvailableWidth) : 1180
    minimumHeight: Screen.desktopAvailableHeight > 0
        ? Math.min(720, Screen.desktopAvailableHeight) : 720
    visible: true
    flags: Qt.Window | Qt.FramelessWindowHint
    title: mediaflow.workspaceViewController.hasProject ? mediaflow.workspaceViewController.projectName : Qt.application.name
    readonly property bool downloadPlanVisible: downloadPlanDialog.visible
    property bool projectVersionsVisible: false
    property bool shortcutReferenceVisible: false
    readonly property int downloadPlanEntryCount: downloadEntries.count
    readonly property bool downloadPlanIsAudio:
        mediaflow.taskController.downloadPlanData.media_kind === "audio"
    readonly property string defaultProjectDirectory: String(
        mediaflow.settingsController.settingsData.defaultProjectDirectory || "")
    readonly property string defaultDownloadDirectory: String(
        mediaflow.settingsController.settingsData.downloadDirectory || "")
    readonly property string downloadDestinationLabel: defaultDownloadDirectory
    property bool windowStateReady: false
    property int restorableWidth: 1600
    property int restorableHeight: 980
    readonly property var downloadResolutionOptions: {
        if (window.downloadPlanIsAudio)
            return [{label: qsTr("仅下载音频"), value: "audio"}]
        const options = [{label: qsTr("最佳可用质量"), value: "best"}]
        const heights = mediaflow.taskController.downloadPlanData.available_heights || []
        for (const height of heights)
            options.push({label: String(height) + "p", value: String(height) + "p"})
        options.push({label: qsTr("仅下载音频"), value: "audio"})
        return options
    }

    AutomationRequestDialog { }
    function indexOfValue(model, value) {
        for (let index = 0; index < model.length; ++index) {
            if (String(model[index].value) === String(value))
                return index
        }
        return 0
    }
    function syncDownloadFormFromSettings() {
        const data = mediaflow.settingsController.settingsData
        downloadResolution.currentIndex = window.indexOfValue(
            window.downloadResolutionOptions,
            window.downloadPlanIsAudio ? "audio" : data.downloadResolution || "best")
        downloadCodec.currentIndex = window.indexOfValue(
            downloadCodec.model, data.downloadCodec || "avc")
        downloadSubtitles.checked = Boolean(data.downloadSubtitles)
        downloadProjectName.clear()
        downloadFilename.clear()
        playlistItems.clear()
    }
    function clampWindowToScreen() {
        if (visibility !== Window.Windowed)
            return;
        const availableWidth = Screen.desktopAvailableWidth > 0
            ? Screen.desktopAvailableWidth : 1600;
        const availableHeight = Screen.desktopAvailableHeight > 0
            ? Screen.desktopAvailableHeight : 980;
        width = Math.min(availableWidth, Math.max(minimumWidth, width));
        height = Math.min(availableHeight, Math.max(minimumHeight, height));
        restorableWidth = width;
        restorableHeight = height;
    }
    color: Theme.window
    palette.window: Theme.window
    palette.windowText: Theme.text
    palette.base: Theme.field
    palette.alternateBase: Theme.surface
    palette.text: Theme.text
    palette.button: Theme.control
    palette.buttonText: Theme.text
    palette.highlight: Theme.accent
    palette.highlightedText: Theme.onAccent
    palette.light: Theme.controlHover
    palette.midlight: Theme.surfaceRaised
    palette.mid: Theme.border
    palette.dark: Theme.surfaceSunken
    palette.shadow: Theme.shadow
    palette.brightText: Theme.textStrong
    palette.link: Theme.accentHover
    palette.linkVisited: Theme.accent
    palette.placeholderText: Theme.textMuted
    palette.toolTipBase: Theme.popup
    palette.toolTipText: Theme.text
    palette.disabled.windowText: Theme.textDisabled
    palette.disabled.text: Theme.textDisabled
    palette.disabled.buttonText: Theme.textDisabled
    Component.onCompleted: {
        const data = mediaflow.settingsController.settingsData
        const availableWidth = Screen.desktopAvailableWidth > 0
            ? Screen.desktopAvailableWidth : 1600
        const availableHeight = Screen.desktopAvailableHeight > 0
            ? Screen.desktopAvailableHeight : 980
        restorableWidth = Math.min(
            availableWidth, Math.max(minimumWidth, Number(data.windowWidth || 1600)))
        restorableHeight = Math.min(
            availableHeight, Math.max(minimumHeight, Number(data.windowHeight || 980)))
        width = restorableWidth
        height = restorableHeight
        windowStateReady = true
        if (Boolean(data.windowMaximized))
            Qt.callLater(window.showMaximized)
    }
    onWidthChanged: {
        if (windowStateReady && visibility === Window.Windowed)
            restorableWidth = width;
    }
    onHeightChanged: {
        if (windowStateReady && visibility === Window.Windowed)
            restorableHeight = height;
    }
    onMinimumWidthChanged: {
        if (windowStateReady)
            Qt.callLater(clampWindowToScreen);
    }
    onMinimumHeightChanged: {
        if (windowStateReady)
            Qt.callLater(clampWindowToScreen);
    }
    onScreenChanged: Qt.callLater(clampWindowToScreen)
    onClosing: mediaflow.settingsController.saveWindowState(
        restorableWidth, restorableHeight, visibility === Window.Maximized)

    ColumnLayout {
        anchors.fill: parent
        spacing: 0
        WindowTitleBar {
            Layout.fillWidth: true
            Layout.preferredHeight: 48
            hostWindow: window
            workspaceItem: pageLoader.item
            onExportRequested: {
                if (pageLoader.item && pageLoader.item.openExportPanel)
                    pageLoader.item.openExportPanel();
            }
            onShortcutReferenceRequested: shortcutReferenceDialog.open()
        }
        Loader {
            id: pageLoader
            objectName: "pageLoader"
            Layout.fillWidth: true
            Layout.fillHeight: true
            source: mediaflow.workspaceViewController.hasProject ? "Workspace.qml" : "HomeView.qml"
        }
    }

    WorkspaceTour {
        id: workspaceTour
        anchors.fill: parent
        workspaceItem: mediaflow.workspaceViewController.hasProject
            && pageLoader.item && pageLoader.item.objectName === "workspace"
            ? pageLoader.item : null
    }

    ShortcutReferenceDialog {
        id: shortcutReferenceDialog
        parent: Overlay.overlay
        anchors.centerIn: Overlay.overlay
        onOpened: window.shortcutReferenceVisible = true
        onClosed: window.shortcutReferenceVisible = false
    }

    Shortcut {
        sequence: "Ctrl+/"
        enabled: mediaflow.workspaceViewController.hasProject
            && !shortcutReferenceDialog.opened
        onActivated: shortcutReferenceDialog.open()
    }

    AppDialog {
        id: collaborationConflictDialog
        objectName: "collaborationConflictDialog"
        title: qsTr("这项内容刚刚被其他协作者修改")
        modal: true
        width: Math.min(620, window.width - 48)
        standardButtons: Dialog.NoButton
        closePolicy: Popup.NoAutoClose
        contentItem: ColumnLayout {
            width: collaborationConflictDialog.availableWidth
            spacing: 12
            Text {
                Layout.fillWidth: true
                text: {
                    const actors = mediaflow.workspaceViewController.collaborationConflict.actors || []
                    return actors.length > 0
                        ? qsTr("%1 在你开始输入后修改了同一项内容。你的输入仍然保留，请选择采用哪一份。").arg(actors.join("、"))
                        : qsTr("项目中的同一项内容在你开始输入后发生了变化。你的输入仍然保留，请选择采用哪一份。")
                }
                color: Theme.textStrong
                font.pixelSize: Theme.fontSizeBody
                wrapMode: Text.WordWrap
            }
            Text {
                Layout.fillWidth: true
                text: {
                    const paths = mediaflow.workspaceViewController.collaborationConflict.paths || []
                    return paths.length > 0
                        ? qsTr("发生冲突的位置：%1").arg(paths.join("\n"))
                        : ""
                }
                visible: text.length > 0
                color: Theme.textMuted
                font.pixelSize: Theme.fontSizeCaption
                wrapMode: Text.WrapAnywhere
            }
            RowLayout {
                Layout.fillWidth: true
                Item { Layout.fillWidth: true }
                AppButton {
                    objectName: "acceptRemoteConflictButton"
                    text: qsTr("采用项目中的最新内容")
                    onClicked: mediaflow.workspaceProjectController.resolveCollaborationConflict("accept_remote")
                }
                AppButton {
                    objectName: "keepLocalConflictButton"
                    primary: true
                    text: qsTr("保留我的输入")
                    onClicked: mediaflow.workspaceProjectController.resolveCollaborationConflict("keep_local")
                }
            }
        }
    }
    Connections {
        target: mediaflow.workspaceProjectController
        function onSampleTourRequested() { workspaceTour.open(); }
    }
    Connections {
        target: mediaflow.workspaceViewController
        function onCollaborationConflictChanged() {
            if (mediaflow.workspaceViewController.collaborationConflictPending)
                collaborationConflictDialog.open()
            else
                collaborationConflictDialog.close()
        }
    }

    FolderDialog {
        id: downloadOutputDirectoryDialog
        title: qsTr("选择媒体默认保存位置")
        onAccepted: mediaflow.settingsController.setDefaultDownloadDirectory(selectedFolder.toLocalFile())
    }

    AppDialog {
        id: downloadPlanDialog
        objectName: "downloadPlanDialog"
        anchors.centerIn: parent
        width: Math.min(900, window.width - 64)
        height: Math.min(760, window.height - 48)
        modal: true
        title: mediaflow.workspaceViewController.hasProject ? qsTr("确认下载") : qsTr("媒体信息与下载设置")
        standardButtons: Dialog.NoButton
        closePolicy: Popup.NoAutoClose
        onOpened: Qt.callLater(window.syncDownloadFormFromSettings)
        contentItem: AppScrollView {
            id: downloadPlanScroll
            objectName: "downloadPlanScroll"
            clip: true
            contentWidth: availableWidth
            ColumnLayout {
            width: downloadPlanScroll.availableWidth
            spacing: 10
            Text {
                Layout.fillWidth: true
                text: mediaflow.taskController.downloadPlanData.title || qsTr("已完成链接分析")
                color: Theme.text
                font.pixelSize: Theme.fontSizeBodyLarge
                font.weight: Font.DemiBold
                wrapMode: Text.WordWrap
            }
            Text {
                objectName: "downloadMediaSummary"
                Layout.fillWidth: true
                text: {
                    const data = mediaflow.taskController.downloadPlanData
                    const parts = [data.kind === "collection"
                        ? qsTr("媒体集合 · %1 项").arg(data.entryCount)
                        : data.media_kind === "audio" ? qsTr("单集音频") : qsTr("单个视频")]
                    if (data.width > 0 && data.height > 0)
                        parts.push(String(data.width) + "×" + String(data.height))
                    if (data.fps > 0)
                        parts.push(Number(data.fps).toFixed(data.fps % 1 ? 2 : 0) + " fps")
                    if (data.duration > 0)
                        parts.push(qsTr("%1 秒").arg(Math.round(data.duration)))
                    parts.push(data.extractor || "yt-dlp")
                    return parts.join(" · ")
                }
                color: Theme.textMuted
                font.pixelSize: Theme.fontSizeCaption
            }
            RowLayout {
                Layout.fillWidth: true
                visible: !mediaflow.workspaceViewController.hasProject
                Text {
                    text: qsTr("项目名称")
                    color: Theme.textMuted
                    Layout.preferredWidth: 120
                }
                AppTextField {
                    id: downloadProjectName
                    objectName: "downloadProjectName"
                    Layout.fillWidth: true
                    placeholderText: qsTr("留空将自动使用“未命名项目 1、2…”")
                }
            }
            AppComboBox {
                id: downloadResolution
                objectName: "downloadResolution"
                Layout.fillWidth: true
                textRole: "label"; valueRole: "value"
                model: window.downloadResolutionOptions
                enabled: !window.downloadPlanIsAudio
            }
            RowLayout {
                Layout.fillWidth: true
                visible: !window.downloadPlanIsAudio
                Text { text: qsTr("视频编码"); color: Theme.textMuted; Layout.preferredWidth: 120 }
                AppComboBox {
                    id: downloadCodec
                    objectName: "downloadCodec"
                    Layout.fillWidth: true
                    textRole: "label"; valueRole: "value"
                    model: [
                        {label: qsTr("最佳可用编码"), value: "best"},
                        {label: qsTr("优先 H.264 / AVC 兼容编码"), value: "avc"}
                    ]
                }
            }
            AppCheckBox {
                id: downloadSubtitles
                objectName: "downloadSubtitles"
                text: qsTr("同时下载字幕和自动字幕，并转换为 SRT")
                visible: !window.downloadPlanIsAudio
            }
            AppTextField {
                id: downloadFilename
                objectName: "downloadFilename"
                Layout.fillWidth: true
                placeholderText: window.downloadPlanIsAudio
                                 ? qsTr("自定义音频文件名（可选）")
                                 : mediaflow.taskController.downloadPlanData.kind === "collection"
                                 ? qsTr("批量文件名前缀（可选）")
                                 : qsTr("自定义文件名（可选）")
            }
            RowLayout {
                Layout.fillWidth: true
                Text {
                    text: qsTr("媒体默认保存位置")
                    color: Theme.textMuted
                    Layout.preferredWidth: 136
                }
                PathDisplay {
                    objectName: "downloadDestinationValue"
                    Layout.fillWidth: true
                    text: window.downloadDestinationLabel
                }
                AppButton {
                    objectName: "chooseDownloadDirectoryButton"
                    text: qsTr("更改")
                    onClicked: downloadOutputDirectoryDialog.open()
                }
                AppButton {
                    objectName: "resetMediaDirectoryButton"
                    visible: window.defaultDownloadDirectory !== mediaflow.settingsController.builtInMediaDirectory
                    text: qsTr("恢复默认")
                    onClicked: mediaflow.settingsController.resetDefaultDownloadDirectory()
                }
            }
            Text {
                Layout.fillWidth: true
                text: qsTr("媒体会保存到应用目录下的 WorkSpace，与 Project 项目目录分开。")
                color: Theme.textMuted
                font.pixelSize: Theme.fontSizeCaption
            }
            RowLayout {
                Layout.fillWidth: true
                visible: mediaflow.taskController.downloadPlanData.kind === "collection"
                Text {
                    text: qsTr("下载项目")
                    color: Theme.text
                    font.pixelSize: Theme.fontSizeBodySmall
                    font.weight: Font.DemiBold
                }
                Item { Layout.fillWidth: true }
                AppButton {
                    text: qsTr("全选")
                    onClicked: mediaflow.taskController.selectAllDownloadEntries(true)
                }
                AppButton {
                    text: qsTr("清空")
                    onClicked: mediaflow.taskController.selectAllDownloadEntries(false)
                }
            }
            ListView {
                id: downloadEntries
                Layout.fillWidth: true
                Layout.preferredHeight: visible ? Math.min(220, Math.max(58, contentHeight)) : 0
                visible: mediaflow.taskController.downloadPlanData.kind === "collection"
                clip: true
                spacing: 4
                model: mediaflow.taskController.downloadEntriesModel
                delegate: Rectangle {
                    required property int entryIndex
                    required property string mediaId
                    required property string title
                    required property string pageUrl
                    required property string uploader
                    required property real duration
                    required property bool available
                    required property string unavailableReason
                    required property bool selected
                    width: downloadEntries.width
                    height: 48
                    radius: Theme.radiusSmall
                    color: Theme.surfaceRaised
                    border.color: selected ? Theme.accent : Theme.border
                    RowLayout {
                        anchors.fill: parent
                        anchors.margins: 7
                        AppCheckBox {
                            checked: parent.parent.selected
                            enabled: parent.parent.available
                            onToggled: mediaflow.taskController.setDownloadEntrySelected(
                                parent.parent.entryIndex, checked)
                        }
                        Text {
                            text: entryIndex + "."
                            color: Theme.textMuted
                            font.pixelSize: Theme.fontSizeCaption
                        }
                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 1
                            Text { Layout.fillWidth: true; text: parent.parent.parent.title; color: parent.parent.parent.available ? Theme.text : Theme.textMuted; font.pixelSize: Theme.fontSizeBodySmall; elide: Text.ElideRight }
                            Text { text: parent.parent.parent.available ? parent.parent.parent.uploader : parent.parent.parent.unavailableReason; color: Theme.textMuted; font.pixelSize: Theme.fontSizeCaption }
                        }
                        Text {
                            text: duration > 0 ? Math.round(duration) + "s" : ""
                            color: Theme.textMuted
                            font.pixelSize: Theme.fontSizeCaption
                        }
                    }
                }
            }
            AppTextField {
                id: playlistItems
                Layout.fillWidth: true
                visible: mediaflow.taskController.downloadPlanData.kind === "collection"
                placeholderText: qsTr("也可手动输入项目范围，例如 1-5,8")
                color: Theme.text
            }
            RowLayout {
                Layout.fillWidth: true
                AppButton {
                    Layout.fillWidth: true
                    text: qsTr("取消")
                    onClicked: mediaflow.taskController.dismissDownloadPlan()
                }
                AppButton {
                    objectName: "confirmDownloadButton"
                    Layout.fillWidth: true
                    primary: true
                    enabled: mediaflow.taskController.downloadPlanReady
                        && (mediaflow.workspaceViewController.hasProject
                            ? Boolean(mediaflow.workspaceViewController.actionCapabilities.canStartTasks)
                            : Boolean(mediaflow.workspaceViewController.actionCapabilities.canCreateProject))
                    text: mediaflow.workspaceViewController.hasProject
                          ? qsTr("开始下载") : qsTr("下载并新建项目")
                    onClicked: {
                        if (!enabled)
                            return;
                        if (mediaflow.workspaceViewController.hasProject) {
                            mediaflow.taskController.submitDownloadPlan(
                                String(downloadResolution.currentValue),
                                playlistItems.text,
                                downloadSubtitles.checked,
                                String(downloadCodec.currentValue),
                                downloadFilename.text)
                        } else {
                            mediaflow.taskController.createProjectAndDownload(
                                window.defaultProjectDirectory,
                                downloadProjectName.text,
                                String(downloadResolution.currentValue),
                                playlistItems.text,
                                downloadSubtitles.checked,
                                String(downloadCodec.currentValue),
                                downloadFilename.text)
                        }
                    }
                }
            }
        }
        }
    }

    AppPopover {
        id: errorPopup
        objectName: "globalErrorPopup"
        x: (window.width - width) / 2
        y: 54
        width: Math.min(560, window.width - 48)
        height: Math.min(300, Math.max(112, errorText.implicitHeight + 76))
        danger: true
        modal: false
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
        contentItem: ColumnLayout {
            spacing: 8
            AppScrollView {
                id: errorScroll
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                contentWidth: availableWidth
                Text {
                    id: errorText
                    width: errorScroll.availableWidth
                    color: Theme.textStrong
                    wrapMode: Text.WordWrap
                    font.pixelSize: Theme.fontSizeBodySmall
                    textFormat: Text.PlainText
                }
            }
            RowLayout {
                Layout.fillWidth: true
                Item { Layout.fillWidth: true }
                AppButton {
                    text: qsTr("前往任务中心")
                    onClicked: {
                        errorPopup.close()
                        mediaflow.taskController.openTaskCenter()
                    }
                }
                AppButton {
                    text: qsTr("复制详情")
                    onClicked: mediaflow.taskController.copyErrorDetails(errorText.text)
                }
                AppButton {
                    text: qsTr("关闭")
                    onClicked: errorPopup.close()
                }
            }
        }
        Timer { id: errorTimer; interval: 12000; onTriggered: errorPopup.close() }
    }

    Connections {
        target: mediaflow.workspaceViewController
        function onErrorOccurred(message) {
            const reference = String(mediaflow.workspaceViewController.lastErrorId || "")
            errorText.text = message + (reference.length > 0
                ? " [" + reference + "]" : "")
            errorPopup.open()
            errorTimer.restart()
        }
    }

    Connections {
        target: mediaflow.taskController
        function onDownloadPlanChanged() {
            if (mediaflow.taskController.downloadPlanReady)
                downloadPlanDialog.open()
            else
                downloadPlanDialog.close()
        }
    }

    Rectangle {
        anchors.fill: parent
        z: 900
        color: Theme.transparent
        border.color: window.visibility === Window.Maximized
            ? Theme.transparent : Theme.borderStrong
        border.width: 1
    }

    WindowResizeHandle {
        hostWindow: window; edges: Qt.TopEdge; cursorShape: Qt.SizeVerCursor
        anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
        height: 5; z: 1000
    }
    WindowResizeHandle {
        hostWindow: window; edges: Qt.BottomEdge; cursorShape: Qt.SizeVerCursor
        anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom
        height: 5; z: 1000
    }
    WindowResizeHandle {
        hostWindow: window; edges: Qt.LeftEdge; cursorShape: Qt.SizeHorCursor
        anchors.left: parent.left; anchors.top: parent.top; anchors.bottom: parent.bottom
        width: 5; z: 1000
    }
    WindowResizeHandle {
        hostWindow: window; edges: Qt.RightEdge; cursorShape: Qt.SizeHorCursor
        anchors.right: parent.right; anchors.top: parent.top; anchors.bottom: parent.bottom
        width: 5; z: 1000
    }
    WindowResizeHandle {
        hostWindow: window; edges: Qt.TopEdge | Qt.LeftEdge; cursorShape: Qt.SizeFDiagCursor
        anchors.left: parent.left; anchors.top: parent.top
        width: 8; height: 8; z: 1001
    }
    WindowResizeHandle {
        hostWindow: window; edges: Qt.TopEdge | Qt.RightEdge; cursorShape: Qt.SizeBDiagCursor
        anchors.right: parent.right; anchors.top: parent.top
        width: 8; height: 8; z: 1001
    }
    WindowResizeHandle {
        hostWindow: window; edges: Qt.BottomEdge | Qt.LeftEdge; cursorShape: Qt.SizeBDiagCursor
        anchors.left: parent.left; anchors.bottom: parent.bottom
        width: 8; height: 8; z: 1001
    }
    WindowResizeHandle {
        hostWindow: window; edges: Qt.BottomEdge | Qt.RightEdge; cursorShape: Qt.SizeFDiagCursor
        anchors.right: parent.right; anchors.bottom: parent.bottom
        width: 8; height: 8; z: 1001
    }
}

import QtQuick
import QtWebEngine
import QtWebChannel
import "."

Rectangle {
    id: root
    objectName: "webEditorCanvas"
    color: Theme.surfaceSunken
    clip: true
    property int playheadFrame: 0
    property int syncGeneration: 0
    property bool bridgeReady: false
    readonly property bool webInputActive:
        visible && webView.activeFocus

    function installBridge() {
        webView.runJavaScript(`
            (() => {
                if (window.__mediaFlowBridgeInstalling || window.__mediaFlowBridgeInstalled)
                    return;
                window.__mediaFlowBridgeInstalling = true;
                const bind = () => new QWebChannel(qt.webChannelTransport, channel => {
                    const bridge = channel.objects.mediaflowWebController;
                    window.__mediaFlowBridge = bridge;
                    window.addEventListener("editablemediaselection", event => {
                        if (!window.__mediaFlowSynchronizing)
                            bridge.selectBrowserLayer(String(event.detail.layerId || ""));
                    });
                    window.addEventListener("editablemediachange", event => {
                        if (!window.__mediaFlowSynchronizing && window.__mediaFlowBinding)
                            bridge.commitBrowserState(JSON.stringify({
                                binding: window.__mediaFlowBinding,
                                state: event.detail.state
                            }));
                    });
                    window.__mediaFlowBridgeInstalled = true;
                    window.__mediaFlowBridgeInstalling = false;
                    bridge.browserBridgeReady();
                });
                if (window.QWebChannel) {
                    bind();
                } else {
                    const script = document.createElement("script");
                    script.src = "qrc:///qtwebchannel/qwebchannel.js";
                    script.onload = bind;
                    document.head.appendChild(script);
                }
            })();
        `);
    }

    function synchronize() {
        if (!visible || webView.loading || !root.bridgeReady || !mediaflow.webController.isWebClip)
            return;
        const generation = ++root.syncGeneration;
        mediaflow.webTimelineController.setActiveFrame(root.playheadFrame);
        const selected = JSON.stringify(mediaflow.webController.selectedLayerId);
        webView.runJavaScript(`
            (() => {
                const generation = ${generation};
                window.__mediaFlowSyncGeneration = Math.max(
                    generation, Number(window.__mediaFlowSyncGeneration || 0));
                return Promise.resolve(window.editableMedia.ready).then(() => {
                    if (generation !== window.__mediaFlowSyncGeneration)
                        return false;
                    window.__mediaFlowSynchronizing = true;
                    try {
                        window.__mediaFlowBinding = ${mediaflow.webController.browserBindingJson};
                        window.editableMedia.setState(${mediaflow.webController.stateJson});
                        window.editableMedia.setTime(${mediaflow.webTimelineController.timeMsForFrame(root.playheadFrame)});
                        if (typeof window.editableMedia.setEditCapabilities === "function")
                            window.editableMedia.setEditCapabilities(${mediaflow.webController.capabilitiesJson});
                        window.editableMedia.setEditMode(${mediaflow.webController.editMode});
                        window.editableMedia.selectLayer(${selected});
                    } finally {
                        window.__mediaFlowSynchronizing = false;
                    }
                    window.__mediaFlowBridge.synchronized(
                        generation, ${mediaflow.webController.browserSnapshotScript});
                }).catch(error => {
                    window.__mediaFlowBridge.synchronizationFailed(generation, String(error));
                });
            })()
        `);
    }

    function synchronizeSelection(layerId) {
        if (!visible || webView.loading || !mediaflow.webController.isWebClip)
            return;
        const selected = JSON.stringify(String(layerId || ""));
        webView.runJavaScript(`
            Promise.resolve(window.editableMedia.ready).then(() =>
                window.editableMedia.selectLayer(${selected}))`);
    }

    function previewRuntimeState(payload) {
        if (!visible || webView.loading || !mediaflow.webController.isWebClip)
            return;
        webView.runJavaScript(`
            Promise.resolve(window.editableMedia.ready).then(() => {
                window.__mediaFlowSynchronizing = true;
                try {
                    window.editableMedia.setState(${payload});
                    window.editableMedia.setTime(
                        ${mediaflow.webTimelineController.timeMsForFrame(root.playheadFrame)});
                } finally {
                    window.__mediaFlowSynchronizing = false;
                }
            })`);
    }

    QtObject {
        id: webBridge
        WebChannel.id: "mediaflowWebController"
        function selectBrowserLayer(layerId) {
            mediaflow.webController.selectBrowserLayer(String(layerId || ""));
        }
        function commitBrowserState(payload) {
            mediaflow.webController.commitBrowserState(String(payload || ""));
        }
        function browserBridgeReady() {
            root.bridgeReady = true;
            mediaflow.webController.browserBridgeReady();
            root.synchronize();
        }
        function synchronized(generation, snapshot) {
            if (generation === root.syncGeneration && snapshot)
                mediaflow.webController.applyBrowserSnapshot(String(snapshot));
        }
        function synchronizationFailed(generation, message) {
            if (generation === root.syncGeneration)
                mediaflow.webController.browserSynchronizationFailed(String(message));
        }
    }

    WebChannel {
        id: channel
        registeredObjects: [webBridge]
    }

    WebEngineView {
        id: webView
        objectName: "webEditorWebView"
        anchors.centerIn: parent
        width: Math.min(parent.width, (mediaflow.webController.activeCanvasData.width || 1080) * zoomFactor)
        height: Math.min(parent.height, (mediaflow.webController.activeCanvasData.height || 1080) * zoomFactor)
        url: mediaflow.webController.entryUrl
        webChannel: channel
        backgroundColor: "transparent"
        zoomFactor: Math.max(0.1, Math.min(
            root.width / (mediaflow.webController.activeCanvasData.width || 1080),
            root.height / (mediaflow.webController.activeCanvasData.height || 1080),
            1
        ))
        onLoadingChanged: function (request) {
            if (request.status === WebEngineView.LoadStartedStatus) {
                ++root.syncGeneration;
                root.bridgeReady = false;
                mediaflow.webController.beginBrowserPage();
            }
            if (request.status === WebEngineView.LoadSucceededStatus)
                root.installBridge();
        }
    }

    Connections {
        target: mediaflow.webController
        function onWebStateChanged() {
            ++root.syncGeneration;
            Qt.callLater(root.synchronize);
        }
        function onBrowserSelectionRequested(layerId) {
            root.synchronizeSelection(layerId);
        }
    }

    Connections {
        target: mediaflow.webTimelineController
        function onBrowserRuntimePreviewRequested(payload) {
            root.previewRuntimeState(payload);
        }
    }

    onPlayheadFrameChanged: {
        if (!visible || webView.loading || !mediaflow.webController.isWebClip)
            return;
        mediaflow.webTimelineController.setActiveFrame(root.playheadFrame);
        webView.runJavaScript(`Promise.resolve(window.editableMedia.ready).then(() =>
            window.editableMedia.setTime(${mediaflow.webTimelineController.timeMsForFrame(root.playheadFrame)}))`);
    }

    Rectangle {
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.bottom: parent.bottom
        anchors.bottomMargin: 12
        width: hint.implicitWidth + 16
        height: hint.implicitHeight + 12
        color: Theme.surface
        radius: Theme.radiusSmall
        opacity: 0.9
        Text {
            id: hint
            anchors.centerIn: parent
            text: qsTr("拖动图层调整位置；释放鼠标后写入项目")
            color: Theme.textMuted
            font.pixelSize: Theme.fontSizeCaption
        }
    }
}

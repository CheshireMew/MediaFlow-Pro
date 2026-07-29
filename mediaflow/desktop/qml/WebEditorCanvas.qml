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
    readonly property bool webInputActive:
        visible && webView.activeFocus

    function installBridge() {
        webView.runJavaScript(`
            (() => {
                if (window.__mediaFlowBridgeInstalling || window.__mediaFlowBridgeInstalled)
                    return;
                window.__mediaFlowBridgeInstalling = true;
                const bind = () => new QWebChannel(qt.webChannelTransport, channel => {
                    const bridge = channel.objects.webController;
                    window.addEventListener("editablemediaselection", event => {
                        if (!window.__mediaFlowSynchronizing)
                            bridge.selectBrowserLayer(String(event.detail.layerId || ""));
                    });
                    window.addEventListener("editablemediachange", event =>
                        bridge.commitBrowserState(JSON.stringify(event.detail.state)));
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
        if (!visible || webView.loading || !webController.isWebClip)
            return;
        const generation = ++root.syncGeneration;
        webController.setActiveFrame(root.playheadFrame);
        const selected = JSON.stringify(webController.selectedLayerId);
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
                        window.editableMedia.setState(${webController.stateJson});
                        window.editableMedia.setTime(${webController.timeMsForFrame(root.playheadFrame)});
                        if (typeof window.editableMedia.setEditCapabilities === "function")
                            window.editableMedia.setEditCapabilities(${webController.capabilitiesJson});
                        window.editableMedia.setEditMode(${webController.editMode});
                        window.editableMedia.selectLayer(${selected});
                    } finally {
                        window.__mediaFlowSynchronizing = false;
                    }
                    return true;
                });
            })()
        `, function (applied) {
            if (!applied || generation !== root.syncGeneration)
                return;
            root.installBridge();
            webView.runJavaScript(webController.browserSnapshotScript, function (snapshot) {
                if (snapshot && generation === root.syncGeneration)
                    webController.applyBrowserSnapshot(String(snapshot));
            });
        });
    }

    function synchronizeSelection(layerId) {
        if (!visible || webView.loading || !webController.isWebClip)
            return;
        const selected = JSON.stringify(String(layerId || ""));
        webView.runJavaScript(`
            Promise.resolve(window.editableMedia.ready).then(() =>
                window.editableMedia.selectLayer(${selected}))`);
    }

    QtObject {
        id: webBridge
        WebChannel.id: "webController"
        function selectBrowserLayer(layerId) {
            webController.selectBrowserLayer(String(layerId || ""));
        }
        function commitBrowserState(payload) {
            webController.commitBrowserState(String(payload || ""));
        }
        function browserBridgeReady() {
            webController.browserBridgeReady();
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
        width: Math.min(parent.width, (webController.activeCanvasData.width || 1080) * zoomFactor)
        height: Math.min(parent.height, (webController.activeCanvasData.height || 1080) * zoomFactor)
        url: webController.entryUrl
        webChannel: channel
        backgroundColor: "transparent"
        zoomFactor: Math.max(0.1, Math.min(
            root.width / (webController.activeCanvasData.width || 1080),
            root.height / (webController.activeCanvasData.height || 1080),
            1
        ))
        onLoadingChanged: function (request) {
            if (request.status === WebEngineView.LoadSucceededStatus)
                root.synchronize();
        }
    }

    Connections {
        target: webController
        function onWebStateChanged() {
            Qt.callLater(root.synchronize);
        }
        function onBrowserSelectionRequested(layerId) {
            root.synchronizeSelection(layerId);
        }
    }

    onPlayheadFrameChanged: {
        if (!visible || webView.loading || !webController.isWebClip)
            return;
        webController.setActiveFrame(root.playheadFrame);
        webView.runJavaScript(`Promise.resolve(window.editableMedia.ready).then(() =>
            window.editableMedia.setTime(${webController.timeMsForFrame(root.playheadFrame)}))`);
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

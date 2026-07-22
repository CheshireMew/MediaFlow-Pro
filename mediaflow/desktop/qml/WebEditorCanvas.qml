import QtQuick
import QtQuick.Controls
import QtWebEngine
import QtWebChannel
import "."

Rectangle {
    id: root
    color: Theme.surfaceSunken
    clip: true
    property int playheadFrame: 0

    function installBridge() {
        webView.runJavaScript(`
            (() => {
                if (window.__mediaFlowBridgeInstalling || window.__mediaFlowBridgeInstalled)
                    return;
                window.__mediaFlowBridgeInstalling = true;
                const bind = () => new QWebChannel(qt.webChannelTransport, channel => {
                    const bridge = channel.objects.webController;
                    window.addEventListener("editablemediaselection", event =>
                        bridge.selectBrowserLayer(String(event.detail.layerId || "")));
                    window.addEventListener("editablemediachange", event =>
                        bridge.commitBrowserState(JSON.stringify(event.detail.state)));
                    window.__mediaFlowBridgeInstalled = true;
                    window.__mediaFlowBridgeInstalling = false;
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
        const selected = JSON.stringify(webController.selectedLayerId);
        webView.runJavaScript(`
            Promise.resolve(window.editableMedia.ready).then(() => {
                window.editableMedia.setState(${webController.stateJson});
                window.editableMedia.setTime(${webController.timeMsForFrame(root.playheadFrame)});
                if (typeof window.editableMedia.setEditCapabilities === "function")
                    window.editableMedia.setEditCapabilities(${webController.capabilitiesJson});
                window.editableMedia.setEditMode(true);
                window.editableMedia.selectLayer(${selected});
                return true;
            })
        `, function () {
            root.installBridge();
            webView.runJavaScript(webController.browserSnapshotScript, function (snapshot) {
                if (snapshot)
                    webController.applyBrowserSnapshot(String(snapshot));
            });
        });
    }

    WebChannel {
        id: channel
        registeredObjects: [webController]
    }

    WebEngineView {
        id: webView
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
    }

    onPlayheadFrameChanged: {
        if (!visible || webView.loading || !webController.isWebClip)
            return;
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

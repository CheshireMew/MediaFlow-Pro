import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "."
import "components"

AppDialog {
    id: root
    objectName: "colorScopesDialog"
    property int playheadFrame: 0
    property var analysis: mediaflow.timelineColorController.scopeData
    anchors.centerIn: parent
    width: 720
    height: Math.min(620, parent ? parent.height - 48 : 620)
    modal: false
    title: qsTr("视频示波器")
    standardButtons: Dialog.Close

    onAnalysisChanged: scopeCanvas.requestPaint()

    contentItem: ColumnLayout {
        spacing: 8
        RowLayout {
            Layout.fillWidth: true
            AppComboBox {
                id: scopeKind
                objectName: "colorScopeKind"
                textRole: "label"
                valueRole: "value"
                model: [
                    { label: qsTr("亮度波形"), value: "waveform" },
                    { label: qsTr("RGB Parade"), value: "parade" },
                    { label: qsTr("矢量示波器"), value: "vectorscope" },
                    { label: qsTr("RGB 直方图"), value: "histogram" }
                ]
                onActivated: scopeCanvas.requestPaint()
            }
            Text {
                Layout.fillWidth: true
                text: root.analysis.statistics
                    ? qsTr("平均亮度 %1% · 黑位裁切 %2% · 白位裁切 %3%")
                        .arg((Number(root.analysis.statistics.average_luma) * 100).toFixed(1))
                        .arg(Number(root.analysis.statistics.black_clip_percent).toFixed(2))
                        .arg(Number(root.analysis.statistics.white_clip_percent).toFixed(2))
                    : qsTr("分析的是预览与导出共同渲染出的实际画面")
                color: Theme.textMuted
                font.pixelSize: Theme.fontSizeCaption
            }
            AppButton {
                objectName: "analyzeColorScopesButton"
                text: mediaflow.timelineColorController.running ? qsTr("分析中…") : qsTr("分析当前帧")
                primary: true
                enabled: !mediaflow.timelineColorController.running
                    && mediaflow.workspaceViewController.timelineDurationFrames > 0
                onClicked: mediaflow.timelineColorController.analyzeFrame(root.playheadFrame)
            }
        }
        Canvas {
            id: scopeCanvas
            objectName: "colorScopeCanvas"
            Layout.fillWidth: true
            Layout.fillHeight: true
            onPaint: {
                const ctx = getContext("2d");
                ctx.reset();
                ctx.fillStyle = "#090b10";
                ctx.fillRect(0, 0, width, height);
                ctx.strokeStyle = "#28303d";
                ctx.lineWidth = 1;
                for (let line = 1; line < 4; ++line) {
                    ctx.beginPath();
                    ctx.moveTo(0, height * line / 4);
                    ctx.lineTo(width, height * line / 4);
                    ctx.stroke();
                }
                const mode = String(scopeKind.currentValue || "waveform");
                if (mode === "histogram") {
                    const series = [root.analysis.histogram_red || [], root.analysis.histogram_green || [], root.analysis.histogram_blue || []];
                    const colors = ["#ff5c6c", "#62d68b", "#59a9ff"];
                    let peak = 1;
                    for (const values of series)
                        for (const value of values) peak = Math.max(peak, Number(value));
                    for (let channel = 0; channel < series.length; ++channel) {
                        ctx.strokeStyle = colors[channel];
                        ctx.beginPath();
                        for (let index = 0; index < series[channel].length; ++index) {
                            const x = index * width / 255;
                            const y = height - Number(series[channel][index]) * height / peak;
                            if (index === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
                        }
                        ctx.stroke();
                    }
                    return;
                }
                if (mode === "parade") {
                    const series = [root.analysis.waveform_red || [], root.analysis.waveform_green || [], root.analysis.waveform_blue || []];
                    const colors = [[255, 82, 100], [92, 221, 139], [72, 156, 255]];
                    const bins = Number(root.analysis.bins || 64);
                    const channelWidth = width / 3;
                    const cellWidth = channelWidth / bins;
                    const cellHeight = height / bins;
                    for (let channel = 0; channel < series.length; ++channel) {
                        const values = series[channel];
                        for (let row = 0; row < values.length; ++row) {
                            for (let column = 0; column < values[row].length; ++column) {
                                const density = Number(values[row][column]);
                                if (density <= 0) continue;
                                const alpha = Math.min(0.92, 0.08 + density / 1100);
                                const color = colors[channel];
                                ctx.fillStyle = "rgba(" + color[0] + "," + color[1] + "," + color[2] + "," + alpha + ")";
                                ctx.fillRect(channel * channelWidth + column * cellWidth,
                                    row * cellHeight, Math.ceil(cellWidth), Math.ceil(cellHeight));
                            }
                        }
                        if (channel > 0) {
                            ctx.strokeStyle = "#3a4352";
                            ctx.beginPath();
                            ctx.moveTo(channel * channelWidth, 0);
                            ctx.lineTo(channel * channelWidth, height);
                            ctx.stroke();
                        }
                    }
                    return;
                }
                const values = mode === "vectorscope"
                    ? (root.analysis.vectorscope || []) : (root.analysis.waveform_luma || []);
                const bins = Number(root.analysis.bins || 64);
                const cellWidth = width / bins;
                const cellHeight = height / bins;
                for (let row = 0; row < values.length; ++row) {
                    for (let column = 0; column < values[row].length; ++column) {
                        const density = Number(values[row][column]);
                        if (density <= 0) continue;
                        const alpha = Math.min(0.9, 0.08 + density / 1100);
                        ctx.fillStyle = mode === "vectorscope"
                            ? "rgba(90,225,190," + alpha + ")"
                            : "rgba(225,235,245," + alpha + ")";
                        ctx.fillRect(column * cellWidth, row * cellHeight,
                            Math.ceil(cellWidth), Math.ceil(cellHeight));
                    }
                }
            }
        }
        Text {
            Layout.fillWidth: true
            visible: Boolean(root.analysis.error)
            text: String(root.analysis.error || "")
            color: Theme.danger
            wrapMode: Text.WordWrap
        }
    }
}

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "."
import "components"

ScrollView {
    id: scrollRoot
    objectName: "audioScroll"
    clip: true
    contentWidth: availableWidth
    ColumnLayout {
        id: root
        objectName: "audioContent"
        width: scrollRoot.availableWidth
        spacing: 10
    function metric(name, suffix) {
        const value = projectController.audioMetrics[name]
        return value === undefined ? "—" : Number(value).toFixed(1) + " " + suffix
    }
    function effectLabel(kind) {
        const labels = {
            "parametric_eq": qsTr("参数均衡器"), "high_pass": qsTr("高通"),
            "low_pass": qsTr("低通"), "compressor": qsTr("压缩器"),
            "limiter": qsTr("限制器"), "noise_gate": qsTr("噪声门"),
            "rnnoise": "RNNoise", "channel_map": qsTr("声道映射"),
            "loudness_normalize": qsTr("响度标准化"), "ducking": qsTr("自动闪避")
        }
        return labels[kind] || kind
    }
    function parameterLabel(key) {
        const labels = {
            "low_db": qsTr("低频增益"), "low_mid_db": qsTr("中低频增益"),
            "high_mid_db": qsTr("中高频增益"), "high_db": qsTr("高频增益"),
            "frequency_hz": qsTr("截止频率"), "threshold_db": qsTr("阈值"),
            "ratio": qsTr("压缩比"), "attack_ms": qsTr("启动时间"),
            "release_ms": qsTr("释放时间"), "ceiling_db": qsTr("上限"),
            "mix": qsTr("混合"), "layout": qsTr("声道布局"),
            "target_lufs": qsTr("目标响度"), "true_peak_db": qsTr("True Peak 上限"),
            "driver_bus_id": qsTr("驱动总线"), "reduction_db": qsTr("衰减量")
        }
        return labels[key] || key
    }
    function selectedEffectKind() {
        const row = projectController.audioEffectsModel.findRow(
            "effectId", projectController.selectedAudioEffectId)
        return row < 0 ? "" : String(projectController.audioEffectsModel.get(row).kind)
    }
    function presetLabel(key) {
        const labels = {
            "default": qsTr("默认"), "dialogue": qsTr("对白"),
            "gentle": qsTr("轻柔"), "strong": qsTr("强力"),
            "social": qsTr("社交平台"), "web": qsTr("网络视频"),
            "broadcast": qsTr("广播"), "mono": qsTr("单声道"),
            "stereo": qsTr("立体声"), "5.1": "5.1"
        }
        return labels[key] || key
    }
    component MetricBox: Rectangle {
        property string label
        property string value
        property string metricObjectName
        Layout.fillWidth: true
        Layout.preferredHeight: 48
        radius: 5
        color: Theme.surface
        Column {
            anchors.centerIn: parent
            spacing: 2
            Text { anchors.horizontalCenter: parent.horizontalCenter; text: parent.parent.label; color: Theme.textMuted; font.pixelSize: 9 }
            Text { objectName: parent.parent.metricObjectName; anchors.horizontalCenter: parent.horizontalCenter; text: parent.parent.value; color: Theme.text; font.pixelSize: 12; font.weight: Font.DemiBold }
        }
    }
    Text { text: qsTr("专业音频"); color: Theme.text; font.pixelSize: 16; font.weight: Font.DemiBold }
    Text { text: qsTr("48 kHz 浮点总线图"); color: Theme.textMuted; font.pixelSize: 10 }
    RowLayout {
        Layout.fillWidth: true
        TextField {
            id: newBusName
            Layout.fillWidth: true
            placeholderText: qsTr("新音频总线名称")
            color: Theme.text
        }
        AppButton {
            text: qsTr("添加总线")
            onClicked: {
                projectController.addAudioBus(newBusName.text)
                newBusName.clear()
            }
        }
    }
    Rectangle {
        Layout.fillWidth: true
        Layout.preferredHeight: 108
        radius: Theme.radiusSmall
        color: Theme.surfaceRaised
        border.color: Theme.border
        ColumnLayout {
            anchors.fill: parent
            anchors.margins: 8
            spacing: 6
            RowLayout {
                Layout.fillWidth: true
                Text { text: qsTr("序列响度"); color: Theme.text; font.pixelSize: 11; font.weight: Font.DemiBold }
                Item { Layout.fillWidth: true }
                Text {
                    text: qsTr("目标 %1 LUFS / %2 dBTP").arg(projectController.settingsData.loudnessTarget).arg(projectController.settingsData.truePeak)
                    color: Theme.textMuted
                    font.pixelSize: 9
                }
                AppButton {
                    text: projectController.audioAnalysisRunning ? qsTr("测量中…") : qsTr("重新测量")
                    enabled: !projectController.audioAnalysisRunning
                    onClicked: projectController.analyzeLoudness()
                }
            }
            GridLayout {
                Layout.fillWidth: true
                columns: 4
                columnSpacing: 8
                MetricBox { label: qsTr("Peak"); value: root.metric("samplePeakDbfs", "dBFS"); metricObjectName: "audioMetricValue0" }
                MetricBox { label: qsTr("True Peak"); value: root.metric("truePeakDbtp", "dBTP"); metricObjectName: "audioMetricValue1" }
                MetricBox { label: qsTr("短期（最高）"); value: root.metric("shortTermLufs", "LUFS"); metricObjectName: "audioMetricValue2" }
                MetricBox { label: qsTr("综合响度"); value: root.metric("integratedLufs", "LUFS"); metricObjectName: "audioMetricValue3" }
            }
        }
    }
    ListView {
        id: busList
        Layout.fillWidth: true
        Layout.preferredHeight: Math.min(240, contentHeight)
        clip: true
        spacing: 6
        model: projectController.audioBusesModel
        delegate: Rectangle {
            required property string busId
            required property string name
            required property string displayName
            required property real gainDb
            required property bool muted
            required property bool solo
            required property string parentBusId
            required property string channelLayout
            width: busList.width
            height: 112
            radius: Theme.radiusSmall
            color: projectController.selectedAudioBusId === busId ? Theme.accentSoft : busMouse.containsMouse ? Theme.surfaceHover : Theme.surfaceRaised
            border.color: projectController.selectedAudioBusId === busId ? Theme.accent : Theme.border
            ColumnLayout {
                anchors.fill: parent; anchors.margins: 8; spacing: 4
                RowLayout {
                    Layout.fillWidth: true
                    Text { Layout.fillWidth: true; text: displayName; color: Theme.text; font.pixelSize: 11; font.weight: Font.DemiBold }
                    AppButton { text: muted ? qsTr("取消静音") : "M"; onClicked: projectController.updateAudioBus(busId, gainDb, !muted, solo) }
                    AppButton { text: solo ? qsTr("取消独奏") : "S"; onClicked: projectController.updateAudioBus(busId, gainDb, muted, !solo) }
                }
                RowLayout {
                    Layout.fillWidth: true
                    Text { text: gainDb.toFixed(1) + " dB"; color: Theme.textMuted; font.pixelSize: 9; Layout.preferredWidth: 48 }
                    Slider {
                        Layout.fillWidth: true; from: -60; to: 12; value: gainDb
                        onPressedChanged: if (!pressed) projectController.updateAudioBus(busId, value, muted, solo)
                    }
                }
                RowLayout {
                    Layout.fillWidth: true
                    Text { text: qsTr("输出到"); color: Theme.textMuted; font.pixelSize: 9 }
                    ComboBox {
                        Layout.fillWidth: true
                        enabled: parentBusId.length > 0
                        model: projectController.audioBusesModel
                        textRole: "displayName"; valueRole: "busId"
                        currentIndex: projectController.audioBusesModel.findRow("busId", parentBusId)
                        onActivated: projectController.updateAudioBus(
                            busId, gainDb, muted, solo, String(currentValue), channelLayout)
                    }
                    ComboBox {
                        Layout.preferredWidth: 82
                        model: ["mono", "stereo", "5.1"]
                        currentIndex: model.indexOf(channelLayout)
                        onActivated: projectController.updateAudioBus(
                            busId, gainDb, muted, solo, parentBusId, currentText)
                    }
                }
            }
            MouseArea { id: busMouse; anchors.fill: parent; anchors.bottomMargin: 72; hoverEnabled: true; onClicked: projectController.selectAudioBus(busId) }
        }
    }
    Text { text: qsTr("轨道路由"); color: Theme.text; font.pixelSize: 12; font.weight: Font.DemiBold }
    ListView {
        id: routeList
        Layout.fillWidth: true
        Layout.preferredHeight: Math.min(112, contentHeight)
        clip: true
        spacing: 4
        model: projectController.tracksModel
        delegate: Rectangle {
            id: routeDelegate
            required property string trackId
            required property string name
            required property string displayName
            required property string kind
            required property bool locked
            required property bool muted
            required property bool solo
            required property string audioBusId
            required property var model
            width: routeList.width
            height: kind === "subtitle" ? 0 : 38
            visible: kind !== "subtitle"
            color: Theme.surfaceRaised
            radius: Theme.radiusSmall
            RowLayout {
                anchors.fill: parent
                anchors.margins: 5
                Text { Layout.fillWidth: true; text: routeDelegate.displayName; color: Theme.text; elide: Text.ElideRight; font.pixelSize: 10 }
                ComboBox {
                    Layout.preferredWidth: 126
                    model: projectController.audioBusesModel
                    textRole: "displayName"
                    valueRole: "busId"
                    currentIndex: projectController.audioBusesModel.findRow("busId", routeDelegate.audioBusId)
                    onActivated: projectController.updateTrack(
                        routeDelegate.trackId, routeDelegate.model.enabled, routeDelegate.locked,
                        routeDelegate.muted, routeDelegate.solo, String(currentValue))
                }
            }
        }
    }
    RowLayout {
        Layout.fillWidth: true
        Text { text: qsTr("效果链"); color: Theme.text; font.pixelSize: 12; font.weight: Font.DemiBold }
        Item { Layout.fillWidth: true }
        ComboBox {
            id: effectKind
            Layout.preferredWidth: 120
            model: [
                { label: qsTr("参数均衡器"), value: "parametric_eq" },
                { label: qsTr("高通"), value: "high_pass" },
                { label: qsTr("低通"), value: "low_pass" },
                { label: qsTr("压缩器"), value: "compressor" },
                { label: qsTr("限制器"), value: "limiter" },
                { label: qsTr("噪声门"), value: "noise_gate" },
                { label: qsTr("RNNoise"), value: "rnnoise" },
                { label: qsTr("声道映射"), value: "channel_map" },
                { label: qsTr("响度标准化"), value: "loudness_normalize" },
                { label: qsTr("自动闪避"), value: "ducking" }
            ]
            textRole: "label"
            valueRole: "value"
        }
        AppButton {
            text: "+"
            enabled: projectController.selectedAudioBusId.length > 0
            onClicked: projectController.addAudioEffect(projectController.selectedAudioBusId, effectKind.currentValue)
        }
    }
    ListView {
        id: effectList
        Layout.fillWidth: true
        Layout.preferredHeight: Math.min(180, contentHeight)
        clip: true
        spacing: 5
        model: projectController.audioEffectsModel
        delegate: Rectangle {
            id: effectDelegate
            required property string effectId
            required property string kind
            required property int position
            required property var model
            width: effectList.width; height: 48; radius: Theme.radiusSmall
            color: projectController.selectedAudioEffectId === effectId ? Theme.accentSoft : Theme.surfaceRaised
            border.color: projectController.selectedAudioEffectId === effectId ? Theme.accent : Theme.border
            z: dragHandle.drag.active ? 10 : 0
            RowLayout {
                anchors.fill: parent; anchors.margins: 8
                Text { text: "⋮⋮"; color: Theme.textMuted; font.pixelSize: 12 }
                Text { Layout.fillWidth: true; text: root.effectLabel(kind); color: model.enabled ? Theme.text : Theme.textMuted; font.pixelSize: 11 }
                Button { text: "↑"; enabled: position > 0; implicitWidth: 28; implicitHeight: 26; onClicked: projectController.moveAudioEffect(effectId, position - 1) }
                Button { text: "↓"; enabled: position + 1 < effectList.count; implicitWidth: 28; implicitHeight: 26; onClicked: projectController.moveAudioEffect(effectId, position + 1) }
                Switch { checked: model.enabled; onToggled: projectController.setAudioEffectEnabled(effectId, checked) }
            }
            MouseArea {
                anchors.left: parent.left; anchors.leftMargin: 34
                anchors.right: parent.right; anchors.rightMargin: 118
                anchors.top: parent.top; anchors.bottom: parent.bottom
                onClicked: projectController.selectAudioEffect(effectId)
            }
            MouseArea {
                id: dragHandle
                width: 30; anchors.left: parent.left; anchors.top: parent.top; anchors.bottom: parent.bottom
                cursorShape: Qt.SizeVerCursor
                drag.target: effectDelegate
                drag.axis: Drag.YAxis
                onPressed: projectController.selectAudioEffect(effectId)
                onReleased: {
                    var target = Math.max(0, Math.min(effectList.count - 1,
                        Math.round(effectDelegate.y / (effectDelegate.height + effectList.spacing))))
                    projectController.moveAudioEffect(effectId, target)
                }
            }
        }
        EmptyState {
            anchors.fill: parent
            visible: effectList.count === 0
            iconText: "音"
            title: qsTr("选择一条音频总线")
            description: qsTr("选择总线后可添加、旁通并配置内置效果。")
        }
    }
    Panel {
        objectName: "audioParameterPanel"
        Layout.fillWidth: true
        Layout.preferredHeight: Math.max(190, parameterList.contentHeight + 58)
        visible: projectController.selectedAudioEffectId.length > 0
        ColumnLayout {
            anchors.fill: parent
            anchors.margins: 8
            spacing: 6
            RowLayout {
                Layout.fillWidth: true
                Text { text: qsTr("参数预设"); color: Theme.textMuted; font.pixelSize: 10 }
                ComboBox {
                    id: effectPreset
                    Layout.fillWidth: true
                    textRole: "label"; valueRole: "presetId"
                    model: projectController.audioEffectPresets(root.selectedEffectKind()).map(
                        function(item) {
                            return { presetId: item.presetId, label: root.presetLabel(item.presetId) }
                        })
                }
                AppButton {
                    text: qsTr("应用")
                    enabled: effectPreset.count > 0
                    onClicked: projectController.applyAudioEffectPreset(
                        projectController.selectedAudioEffectId, String(effectPreset.currentValue))
                }
                AppButton {
                    text: qsTr("移除效果")
                    onClicked: projectController.removeAudioEffect(projectController.selectedAudioEffectId)
                }
            }
            ListView {
                id: parameterList
                objectName: "audioParameterList"
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                spacing: 6
                model: projectController.audioEffectParametersModel
                delegate: ColumnLayout {
                    id: parameterDelegate
                    required property string key
                    required property real minimum
                    required property real maximum
                    required property real step
                    required property string unit
                    required property string valueType
                    required property var model
                    width: parameterList.width
                    height: 58
                    spacing: 2
                    RowLayout {
                        Layout.fillWidth: true
                        Text { Layout.fillWidth: true; text: root.parameterLabel(parameterDelegate.key); color: Theme.text; font.pixelSize: 10 }
                        Text {
                            visible: parameterDelegate.valueType === "number"
                            text: Number(parameterDelegate.model.value).toFixed(parameterDelegate.step < 1 ? 1 : 0) + " " + parameterDelegate.unit
                            color: Theme.textMuted; font.pixelSize: 9
                        }
                    }
                    Slider {
                        Layout.fillWidth: true
                        visible: parameterDelegate.valueType === "number"
                        from: parameterDelegate.minimum; to: parameterDelegate.maximum; stepSize: parameterDelegate.step
                        value: Number(parameterDelegate.model.value)
                        onPressedChanged: if (!pressed) projectController.setAudioEffectParameter(
                            projectController.selectedAudioEffectId, parameterDelegate.key, value)
                    }
                    ComboBox {
                        Layout.fillWidth: true
                        visible: parameterDelegate.valueType === "layout"
                        model: ["mono", "stereo", "5.1"]
                        currentIndex: model.indexOf(String(parameterDelegate.model.value))
                        onActivated: projectController.setAudioEffectParameter(
                            projectController.selectedAudioEffectId, parameterDelegate.key, currentText)
                    }
                    ComboBox {
                        Layout.fillWidth: true
                        visible: parameterDelegate.valueType === "bus"
                        model: projectController.audioBusesModel
                        textRole: "displayName"; valueRole: "busId"
                        currentIndex: projectController.audioBusesModel.findRow(
                            "busId", String(parameterDelegate.model.value))
                        onActivated: projectController.setAudioEffectParameter(
                            projectController.selectedAudioEffectId, parameterDelegate.key, String(currentValue))
                    }
                }
            }
        }
    }
    }
}

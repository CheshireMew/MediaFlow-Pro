import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "."
import "components"

AppScrollView {
    id: scrollRoot
    objectName: "audioScroll"
    clip: true
    contentWidth: availableWidth
    ColumnLayout {
        id: root
        objectName: "audioContent"
        width: scrollRoot.availableWidth
        spacing: 10
        property var taskData: ({})
        readonly property bool canEdit: Boolean(mediaflow.workspaceViewController.actionCapabilities.canEdit)
        readonly property bool canStartTasks: Boolean(mediaflow.workspaceViewController.actionCapabilities.canStartTasks)
        function refreshTask() {
            taskData = mediaflow.taskController.latestCommandTask(
                "analyze_loudness", mediaflow.workspaceViewController.activeSequenceId);
        }
        Connections {
            target: mediaflow.taskController
            function onTasksChanged() { root.refreshTask(); }
        }
        Component.onCompleted: refreshTask()
        function metric(name, suffix) {
            const value = mediaflow.audioController.audioMetrics[name];
            return value === undefined ? "—" : Number(value).toFixed(1) + " " + suffix;
        }
        function selectedEffectKind() {
            const row = mediaflow.audioController.audioEffectsModel.findRow("effectId", mediaflow.audioController.selectedAudioEffectId);
            return row < 0 ? "" : String(mediaflow.audioController.audioEffectsModel.get(row).kind);
        }
        readonly property var channelLayoutOptions: [
            {
                label: qsTr("单声道"),
                value: "mono"
            },
            {
                label: qsTr("立体声"),
                value: "stereo"
            },
            {
                label: "5.1",
                value: "5.1"
            }
        ]
        function channelLayoutIndex(value) {
            for (var index = 0; index < channelLayoutOptions.length; ++index) {
                if (channelLayoutOptions[index].value === String(value))
                    return index;
            }
            return 0;
        }
        Panel {
            objectName: "audioClipProperties"
            Layout.fillWidth: true
            implicitHeight: clipAudioContent.implicitHeight + 22
            visible: mediaflow.timelineViewController.selectedClipId.length > 0
            enabled: root.canEdit
            ColumnLayout {
                id: clipAudioContent
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.top: parent.top
                anchors.margins: 11
                spacing: 7
                Text {
                    Layout.fillWidth: true
                    text: mediaflow.timelineViewController.selectedClipData.assetName || qsTr("所选片段")
                    color: Theme.text
                    font.pixelSize: Theme.fontSizeBodySmall
                    font.weight: Font.DemiBold
                    elide: Text.ElideRight
                }
                Text {
                    text: qsTr("片段音频")
                    color: Theme.textMuted
                    font.pixelSize: Theme.fontSizeCaption
                }
                GridLayout {
                    Layout.fillWidth: true
                    columns: 2
                    columnSpacing: 7
                    rowSpacing: 6
                    PropertyField {
                        id: clipGain
                        objectName: "audioClipGain"
                        Layout.fillWidth: true
                        label: qsTr("增益 dB")
                        text: String(mediaflow.timelineViewController.selectedClipData.gainDb ?? 0)
                    }
                    PropertyField {
                        id: clipPan
                        objectName: "audioClipPan"
                        Layout.fillWidth: true
                        label: qsTr("声像")
                        text: String(mediaflow.timelineViewController.selectedClipData.pan ?? 0)
                    }
                    PropertyField {
                        id: clipFadeIn
                        Layout.fillWidth: true
                        label: qsTr("淡入帧")
                        text: String(mediaflow.timelineViewController.selectedClipData.fadeInFrames ?? 0)
                    }
                    PropertyField {
                        id: clipFadeOut
                        Layout.fillWidth: true
                        label: qsTr("淡出帧")
                        text: String(mediaflow.timelineViewController.selectedClipData.fadeOutFrames ?? 0)
                    }
                }
                AppButton {
                    objectName: "applyClipAudioButton"
                    Layout.fillWidth: true
                    primary: true
                    text: qsTr("应用片段音频参数")
                    onClicked: mediaflow.timelineClipController.setClipAudio(mediaflow.timelineViewController.selectedClipId, Number(clipGain.text), Number(clipPan.text), Number(clipFadeIn.text), Number(clipFadeOut.text))
                }
            }
        }
        Text {
            text: qsTr("48 kHz 浮点总线图")
            color: Theme.textMuted
            font.pixelSize: Theme.fontSizeCaption
        }
        RowLayout {
            Layout.fillWidth: true
            AppTextField {
                id: newBusName
                Layout.fillWidth: true
                placeholderText: qsTr("新音频总线名称")
                color: Theme.text
            }
            AppButton {
                text: qsTr("添加总线")
                enabled: root.canEdit
                onClicked: {
                    mediaflow.audioController.addAudioBus(newBusName.text);
                    newBusName.clear();
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
                    Text {
                        text: qsTr("序列响度")
                        color: Theme.text
                        font.pixelSize: Theme.fontSizeCaption
                        font.weight: Font.DemiBold
                    }
                    Item {
                        Layout.fillWidth: true
                    }
                    Text {
                        text: qsTr("目标 %1 LUFS / %2 dBTP").arg(mediaflow.settingsController.settingsData.loudnessTarget).arg(mediaflow.settingsController.settingsData.truePeak)
                        color: Theme.textMuted
                        font.pixelSize: Theme.fontSizeCaption
                    }
                    AppButton {
                        text: mediaflow.audioController.audioAnalysisRunning ? qsTr("测量中…") : qsTr("重新测量")
                        enabled: root.canStartTasks && !mediaflow.audioController.audioAnalysisRunning
                        onClicked: mediaflow.audioController.analyzeLoudness()
                    }
                }
                GridLayout {
                    Layout.fillWidth: true
                    columns: 4
                    columnSpacing: 8
                    MetricBox {
                        label: qsTr("Peak")
                        value: root.metric("samplePeakDbfs", "dBFS")
                        metricObjectName: "audioMetricValue0"
                    }
                    MetricBox {
                        label: qsTr("True Peak")
                        value: root.metric("truePeakDbtp", "dBTP")
                        metricObjectName: "audioMetricValue1"
                    }
                    MetricBox {
                        label: qsTr("短期（最高）")
                        value: root.metric("shortTermLufs", "LUFS")
                        metricObjectName: "audioMetricValue2"
                    }
                    MetricBox {
                        label: qsTr("综合响度")
                        value: root.metric("integratedLufs", "LUFS")
                        metricObjectName: "audioMetricValue3"
                    }
                }
            }
        }
        ContextTaskCard {
            objectName: "audioAnalysisTaskPanel"
            Layout.fillWidth: true
            taskData: root.taskData
            fallbackTitle: qsTr("响度分析任务")
            showArtifact: false
        }
        ListView {
            id: busList
            Layout.fillWidth: true
            Layout.preferredHeight: Math.min(540, contentHeight)
            clip: true
            spacing: 6
            model: mediaflow.audioController.audioBusesModel
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
                height: 126
                radius: Theme.radiusSmall
                color: mediaflow.audioController.selectedAudioBusId === busId ? Theme.accentSoft : busMouse.containsMouse ? Theme.surfaceHover : Theme.surfaceRaised
                border.color: mediaflow.audioController.selectedAudioBusId === busId ? Theme.accent : Theme.border
                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 8
                    spacing: 4
                    RowLayout {
                        Layout.fillWidth: true
                        Text {
                            Layout.fillWidth: true
                            text: displayName
                            color: Theme.text
                            font.pixelSize: Theme.fontSizeCaption
                            font.weight: Font.DemiBold
                        }
                        AppButton {
                            text: muted ? qsTr("取消静音") : qsTr("静音")
                            enabled: root.canEdit
                            Accessible.name: muted ? qsTr("取消静音") : qsTr("静音")
                            ToolTip.visible: hovered
                            ToolTip.text: Accessible.name
                            onClicked: mediaflow.audioController.updateAudioBus(busId, gainDb, !muted, solo)
                        }
                        AppButton {
                            text: solo ? qsTr("取消独奏") : qsTr("独奏")
                            enabled: root.canEdit
                            Accessible.name: solo ? qsTr("取消独奏") : qsTr("独奏")
                            ToolTip.visible: hovered
                            ToolTip.text: Accessible.name
                            onClicked: mediaflow.audioController.updateAudioBus(busId, gainDb, muted, !solo)
                        }
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        Text {
                            text: gainDb.toFixed(1) + " dB"
                            color: Theme.textMuted
                            font.pixelSize: Theme.fontSizeCaption
                            Layout.preferredWidth: 48
                        }
                        AppSlider {
                            Layout.fillWidth: true
                            enabled: root.canEdit
                            from: -60
                            to: 12
                            value: gainDb
                            onPressedChanged: if (!pressed)
                                mediaflow.audioController.updateAudioBus(busId, value, muted, solo)
                        }
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        Text {
                            text: qsTr("输出到")
                            color: Theme.textMuted
                            font.pixelSize: Theme.fontSizeCaption
                        }
                        Text {
                            Layout.fillWidth: true
                            visible: parentBusId.length === 0
                            text: qsTr("主输出（直接输出）")
                            color: Theme.textMuted
                            font.pixelSize: Theme.fontSizeCaption
                        }
                        AppComboBox {
                            Layout.fillWidth: true
                            enabled: root.canEdit
                            visible: parentBusId.length > 0
                            model: mediaflow.audioController.audioBusesModel
                            textRole: "displayName"
                            valueRole: "busId"
                            currentIndex: mediaflow.audioController.audioBusesModel.findRow("busId", parentBusId)
                            onActivated: mediaflow.audioController.updateAudioBus(busId, gainDb, muted, solo, String(currentValue), channelLayout)
                        }
                        AppComboBox {
                            Layout.preferredWidth: 124
                            enabled: root.canEdit
                            model: root.channelLayoutOptions
                            textRole: "label"
                            valueRole: "value"
                            currentIndex: root.channelLayoutIndex(channelLayout)
                            onActivated: mediaflow.audioController.updateAudioBus(busId, gainDb, muted, solo, parentBusId, String(currentValue))
                        }
                    }
                }
                MouseArea {
                    id: busMouse
                    anchors.fill: parent
                    anchors.bottomMargin: 72
                    hoverEnabled: true
                    onClicked: mediaflow.audioController.selectAudioBus(busId)
                }
            }
        }
        Text {
            text: qsTr("轨道路由")
            color: Theme.text
            font.pixelSize: Theme.fontSizeBodySmall
            font.weight: Font.DemiBold
        }
        ListView {
            id: routeList
            Layout.fillWidth: true
            Layout.preferredHeight: Math.min(112, contentHeight)
            clip: true
            spacing: 4
            model: mediaflow.timelineViewController.tracksModel
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
                    Text {
                        Layout.fillWidth: true
                        text: routeDelegate.displayName
                        color: Theme.text
                        elide: Text.ElideRight
                        font.pixelSize: Theme.fontSizeCaption
                    }
                    AppComboBox {
                        Layout.preferredWidth: 126
                        enabled: root.canEdit
                        model: mediaflow.audioController.audioBusesModel
                        textRole: "displayName"
                        valueRole: "busId"
                        currentIndex: mediaflow.audioController.audioBusesModel.findRow("busId", routeDelegate.audioBusId)
                        onActivated: mediaflow.timelineStructureController.updateTrack(routeDelegate.trackId, routeDelegate.model.enabled, routeDelegate.locked, routeDelegate.muted, routeDelegate.solo, String(currentValue))
                    }
                }
            }
        }
        RowLayout {
            Layout.fillWidth: true
            Text {
                text: qsTr("效果链")
                color: Theme.text
                font.pixelSize: Theme.fontSizeBodySmall
                font.weight: Font.DemiBold
            }
            Item {
                Layout.fillWidth: true
            }
            AppComboBox {
                id: effectKind
                Layout.preferredWidth: 120
                model: [
                    {
                        label: qsTr("参数均衡器"),
                        value: "parametric_eq"
                    },
                    {
                        label: qsTr("高通"),
                        value: "high_pass"
                    },
                    {
                        label: qsTr("低通"),
                        value: "low_pass"
                    },
                    {
                        label: qsTr("压缩器"),
                        value: "compressor"
                    },
                    {
                        label: qsTr("限制器"),
                        value: "limiter"
                    },
                    {
                        label: qsTr("噪声门"),
                        value: "noise_gate"
                    },
                    {
                        label: qsTr("RNNoise"),
                        value: "rnnoise"
                    },
                    {
                        label: qsTr("声道映射"),
                        value: "channel_map"
                    },
                    {
                        label: qsTr("响度标准化"),
                        value: "loudness_normalize"
                    },
                    {
                        label: qsTr("自动闪避"),
                        value: "ducking"
                    }
                ]
                textRole: "label"
                valueRole: "value"
            }
            AppIconButton {
                iconName: "add"
                flat: false
                Accessible.name: qsTr("添加音频效果")
                toolTipText: Accessible.name
                enabled: root.canEdit && mediaflow.audioController.selectedAudioBusId.length > 0
                onClicked: mediaflow.audioController.addAudioEffect(mediaflow.audioController.selectedAudioBusId, effectKind.currentValue)
            }
        }
        ListView {
            id: effectList
            Layout.fillWidth: true
            Layout.preferredHeight: Math.min(180, contentHeight)
            clip: true
            spacing: 5
            model: mediaflow.audioController.audioEffectsModel
            delegate: Rectangle {
                id: effectDelegate
                required property string effectId
                required property string kind
                required property string displayName
                required property int position
                required property var model
                width: effectList.width
                height: 48
                radius: Theme.radiusSmall
                color: mediaflow.audioController.selectedAudioEffectId === effectId ? Theme.accentSoft : Theme.surfaceRaised
                border.color: mediaflow.audioController.selectedAudioEffectId === effectId ? Theme.accent : Theme.border
                z: dragHandle.drag.active ? 10 : 0
                RowLayout {
                    anchors.fill: parent
                    anchors.margins: 8
                    AppIcon {
                        Layout.preferredWidth: 18
                        Layout.preferredHeight: 18
                        iconName: "drag"
                        iconColor: Theme.textMuted
                    }
                    Text {
                        Layout.fillWidth: true
                        text: displayName
                        color: model.enabled ? Theme.text : Theme.textMuted
                        font.pixelSize: Theme.fontSizeCaption
                    }
                    AppIconButton {
                        iconName: "up"
                        flat: false
                        Accessible.name: qsTr("上移音频效果")
                        toolTipText: Accessible.name
                        enabled: root.canEdit && position > 0
                        implicitWidth: 28
                        implicitHeight: 26
                        onClicked: mediaflow.audioController.moveAudioEffect(effectId, position - 1)
                    }
                    AppIconButton {
                        iconName: "down"
                        flat: false
                        Accessible.name: qsTr("下移音频效果")
                        toolTipText: Accessible.name
                        enabled: root.canEdit && position + 1 < effectList.count
                        implicitWidth: 28
                        implicitHeight: 26
                        onClicked: mediaflow.audioController.moveAudioEffect(effectId, position + 1)
                    }
                    AppSwitch {
                        checked: model.enabled
                        enabled: root.canEdit
                        Accessible.name: checked ? qsTr("停用音频效果") : qsTr("启用音频效果")
                        onToggled: mediaflow.audioController.setAudioEffectEnabled(effectId, checked)
                    }
                }
                MouseArea {
                    anchors.left: parent.left
                    anchors.leftMargin: 34
                    anchors.right: parent.right
                    anchors.rightMargin: 118
                    anchors.top: parent.top
                    anchors.bottom: parent.bottom
                    onClicked: mediaflow.audioController.selectAudioEffect(effectId)
                }
                MouseArea {
                    id: dragHandle
                    enabled: root.canEdit
                    width: 30
                    anchors.left: parent.left
                    anchors.top: parent.top
                    anchors.bottom: parent.bottom
                    cursorShape: Qt.SizeVerCursor
                    drag.target: effectDelegate
                    drag.axis: Drag.YAxis
                    onPressed: mediaflow.audioController.selectAudioEffect(effectId)
                    onReleased: {
                        var target = Math.max(0, Math.min(effectList.count - 1, Math.round(effectDelegate.y / (effectDelegate.height + effectList.spacing))));
                        mediaflow.audioController.moveAudioEffect(effectId, target);
                    }
                }
            }
            EmptyState {
                anchors.fill: parent
                visible: effectList.count === 0
                iconName: "audio"
                title: qsTr("选择一条音频总线")
                description: qsTr("选择总线后可添加、旁通并配置内置效果。")
            }
        }
        Panel {
            objectName: "audioParameterPanel"
            Layout.fillWidth: true
            Layout.preferredHeight: Math.max(190, parameterList.contentHeight + 58)
            visible: mediaflow.audioController.selectedAudioEffectId.length > 0
            enabled: root.canEdit
            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 8
                spacing: 6
                RowLayout {
                    Layout.fillWidth: true
                    Text {
                        text: qsTr("参数预设")
                        color: Theme.textMuted
                        font.pixelSize: Theme.fontSizeCaption
                    }
                    AppComboBox {
                        id: effectPreset
                        Layout.fillWidth: true
                        textRole: "label"
                        valueRole: "presetId"
                        model: mediaflow.audioController.audioEffectPresets(root.selectedEffectKind())
                    }
                    AppButton {
                        text: qsTr("应用")
                        enabled: effectPreset.count > 0
                        onClicked: mediaflow.audioController.applyAudioEffectPreset(mediaflow.audioController.selectedAudioEffectId, String(effectPreset.currentValue))
                    }
                    AppButton {
                        text: qsTr("移除效果")
                        onClicked: mediaflow.audioController.removeAudioEffect(mediaflow.audioController.selectedAudioEffectId)
                    }
                }
                ListView {
                    id: parameterList
                    objectName: "audioParameterList"
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    clip: true
                    spacing: 6
                    model: mediaflow.audioController.audioEffectParametersModel
                    delegate: ColumnLayout {
                        id: parameterDelegate
                        required property string key
                        required property var descriptor
                        required property var value
                        required property var options
                        required property var model
                        width: parameterList.width
                        height: implicitHeight
                        spacing: 2
                        EditorFieldControl {
                            Layout.fillWidth: true
                            field: ({
                                "path": "audio-effects." + mediaflow.audioController.selectedAudioEffectId + "." + parameterDelegate.key,
                                "target": "audio-effect",
                                "source_id": parameterDelegate.key,
                                "descriptor": parameterDelegate.descriptor,
                                "value": parameterDelegate.value,
                                "locked": false
                            })
                            options: parameterDelegate.options
                            onValueCommitted: value => mediaflow.audioController.setAudioEffectParameter(
                                mediaflow.audioController.selectedAudioEffectId,
                                parameterDelegate.key,
                                value)
                        }
                    }
                }
            }
        }
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
            Text {
                anchors.horizontalCenter: parent.horizontalCenter
                text: parent.parent.label
                color: Theme.textMuted
                font.pixelSize: Theme.fontSizeCaption
            }
            Text {
                objectName: parent.parent.metricObjectName
                anchors.horizontalCenter: parent.horizontalCenter
                text: parent.parent.value
                color: Theme.text
                font.pixelSize: Theme.fontSizeBodySmall
                font.weight: Font.DemiBold
            }
        }
    }
}

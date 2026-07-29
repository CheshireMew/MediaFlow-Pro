import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "."
import "components"

AppDialog {
    id: root
    anchors.centerIn: parent
    implicitWidth: 440
    width: 440
    modal: true
    title: qsTr("序列配置")
    standardButtons: Dialog.Save | Dialog.Cancel

    onOpened: {
        profileWidth.text = String(workspaceController.profileWidth);
        profileHeight.text = String(workspaceController.profileHeight);
        for (var index = 0; index < frameRate.model.length; ++index) {
            var item = frameRate.model[index];
            if (item.n === workspaceController.profileFpsNumerator
                    && item.d === workspaceController.profileFpsDenominator) {
                frameRate.currentIndex = index;
                break;
            }
        }
        colorProfile.currentIndex =
            workspaceController.colorMode === "hdr10_bt2020_pq" ? 1 : 0;
        for (var channelIndex = 0;
                channelIndex < audioChannels.model.length;
                ++channelIndex) {
            if (audioChannels.model[channelIndex].value
                    === workspaceController.profileAudioChannels) {
                audioChannels.currentIndex = channelIndex;
                break;
            }
        }
    }

    onAccepted: {
        var fps = frameRate.model[frameRate.currentIndex];
        var color = colorProfile.model[colorProfile.currentIndex];
        var channels = audioChannels.model[audioChannels.currentIndex];
        workspaceController.updateSequenceProfile(
            Number(profileWidth.text),
            Number(profileHeight.text),
            fps.n,
            fps.d,
            color.value,
            channels.value);
    }

    contentItem: ColumnLayout {
        spacing: 10

        Text {
            text: qsTr("画布比例")
            color: Theme.textMuted
            font.pixelSize: Theme.fontSizeCaption
        }

        AppComboBox {
            Layout.fillWidth: true
            textRole: "label"
            model: [
                {
                    label: "16:9 · 1920×1080",
                    width: 1920,
                    height: 1080
                },
                {
                    label: "9:16 · 1080×1920",
                    width: 1080,
                    height: 1920
                },
                {
                    label: "1:1 · 1080×1080",
                    width: 1080,
                    height: 1080
                },
                {
                    label: "4:5 · 1080×1350",
                    width: 1080,
                    height: 1350
                }
            ]
            onActivated: function (index) {
                profileWidth.text = String(model[index].width);
                profileHeight.text = String(model[index].height);
            }
        }

        RowLayout {
            Layout.fillWidth: true

            AppTextField {
                id: profileWidth
                Layout.fillWidth: true
                validator: IntValidator {
                    bottom: 16
                    top: 16384
                }
                placeholderText: qsTr("宽度")
            }

            Text {
                text: "×"
                color: Theme.textMuted
            }

            AppTextField {
                id: profileHeight
                Layout.fillWidth: true
                validator: IntValidator {
                    bottom: 16
                    top: 16384
                }
                placeholderText: qsTr("高度")
            }
        }

        Text {
            text: qsTr("帧率")
            color: Theme.textMuted
            font.pixelSize: Theme.fontSizeCaption
        }

        AppComboBox {
            id: frameRate
            Layout.fillWidth: true
            textRole: "label"
            model: [
                {
                    label: "23.976 fps",
                    n: 24000,
                    d: 1001
                },
                {
                    label: "24 fps",
                    n: 24,
                    d: 1
                },
                {
                    label: "25 fps",
                    n: 25,
                    d: 1
                },
                {
                    label: "29.97 fps",
                    n: 30000,
                    d: 1001
                },
                {
                    label: "30 fps",
                    n: 30,
                    d: 1
                },
                {
                    label: "50 fps",
                    n: 50,
                    d: 1
                },
                {
                    label: "59.94 fps",
                    n: 60000,
                    d: 1001
                },
                {
                    label: "60 fps",
                    n: 60,
                    d: 1
                }
            ]
            currentIndex: 4
        }

        Text {
            text: qsTr("色彩与输出声道")
            color: Theme.textMuted
            font.pixelSize: Theme.fontSizeCaption
        }

        RowLayout {
            Layout.fillWidth: true

            AppComboBox {
                id: colorProfile
                Layout.fillWidth: true
                textRole: "label"
                model: [
                    {
                        label: "SDR · BT.709",
                        value: "sdr_bt709"
                    },
                    {
                        label: "HDR10 · BT.2020 · PQ",
                        value: "hdr10_bt2020_pq"
                    }
                ]
            }

            AppComboBox {
                id: audioChannels
                Layout.fillWidth: true
                textRole: "label"
                model: [
                    {
                        label: qsTr("单声道"),
                        value: 1
                    },
                    {
                        label: qsTr("立体声"),
                        value: 2
                    },
                    {
                        label: "5.1",
                        value: 6
                    }
                ]
                currentIndex: 1
            }
        }

        Text {
            Layout.fillWidth: true
            text: qsTr("修改帧率会按实际时长重新换算片段、转场和字幕；预览缓存会按需重建。")
            color: Theme.textMuted
            font.pixelSize: Theme.fontSizeCaption
            wrapMode: Text.WordWrap
        }
    }
}

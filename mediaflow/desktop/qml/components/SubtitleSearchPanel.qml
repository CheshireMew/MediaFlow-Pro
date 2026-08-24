import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import ".."

Panel {
    id: search
    property bool expanded: false
    property bool canEdit: false
    property var matches: []
    property int matchIndex: -1

    function refreshSearch() {
        search.matches = mediaflow.subtitleEditingController.findSubtitleMatches(findText.text, matchCase.checked);
        if (search.matches.length === 0)
            search.matchIndex = -1;
        else if (search.matchIndex < 0 || search.matchIndex >= search.matches.length)
            search.matchIndex = 0;
    }

    function activateSearchMatch(index) {
        if (search.matches.length === 0)
            return;
        const count = search.matches.length;
        search.matchIndex = ((index % count) + count) % count;
        mediaflow.subtitleViewController.selectSubtitleSegment(String(search.matches[search.matchIndex].segmentId), false);
    }
    Layout.fillWidth: true
    implicitHeight: search.expanded ? 152 : 0
    visible: search.expanded
    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 8
        spacing: 6
        RowLayout {
            Layout.fillWidth: true
            AppTextField {
                id: findText
                Layout.fillWidth: true
                placeholderText: qsTr("查找")
                onTextChanged: search.refreshSearch()
            }
            AppTextField {
                id: replaceText
                Layout.fillWidth: true
                placeholderText: qsTr("替换为")
            }
        }
        RowLayout {
            Layout.fillWidth: true
            AppCheckBox {
                id: matchCase
                text: qsTr("区分大小写")
                onToggled: search.refreshSearch()
            }
            Text {
                text: qsTr("找到 %1 处").arg(search.matches.length)
                color: Theme.textMuted
                font.pixelSize: Theme.fontSizeCaption
            }
            Item {
                Layout.fillWidth: true
            }
            AppButton {
                text: qsTr("全部替换")
                primary: true
                enabled: search.canEdit && findText.text.length > 0 && search.matches.length > 0
                onClicked: {
                    mediaflow.subtitleEditingController.replaceSubtitleText(findText.text, replaceText.text, matchCase.checked);
                    search.matches = mediaflow.subtitleEditingController.findSubtitleMatches(findText.text, matchCase.checked);
                }
            }
        }
        RowLayout {
            Layout.fillWidth: true
            AppButton {
                text: qsTr("上一个")
                enabled: search.matches.length > 0
                onClicked: search.activateSearchMatch(search.matchIndex - 1)
            }
            AppButton {
                text: qsTr("下一个")
                enabled: search.matches.length > 0
                onClicked: search.activateSearchMatch(search.matchIndex + 1)
            }
            Text {
                Layout.fillWidth: true
                text: search.matchIndex >= 0 ? qsTr("当前 %1 / %2").arg(search.matchIndex + 1).arg(search.matches.length) : qsTr("没有当前匹配")
                color: Theme.textMuted
                font.pixelSize: Theme.fontSizeCaption
            }
            AppButton {
                text: qsTr("替换当前")
                primary: true
                enabled: search.canEdit && search.matchIndex >= 0
                onClicked: {
                    const match = search.matches[search.matchIndex];
                    mediaflow.subtitleEditingController.replaceSubtitleMatch(String(match.segmentId), Number(match.start), Number(match.end), findText.text, replaceText.text, matchCase.checked);
                    Qt.callLater(function () {
                        search.refreshSearch();
                        search.activateSearchMatch(search.matchIndex);
                    });
                }
            }
        }
    }
}

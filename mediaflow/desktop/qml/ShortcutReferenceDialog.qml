import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "."
import "components"

AppDialog {
    id: root
    objectName: "shortcutReferenceDialog"
    title: qsTr("键盘快捷键")
    width: Math.min(720, parent ? parent.width - 40 : 720)
    height: Math.min(720, parent ? parent.height - 40 : 720)
    modal: true
    closePolicy: Popup.CloseOnEscape
    standardButtons: Dialog.NoButton

    readonly property var shortcutRows: [
        {category: qsTr("播放与定位"), action: qsTr("播放或暂停"), keys: qsTr("空格")},
        {category: qsTr("播放与定位"), action: qsTr("反向播放 / 暂停 / 正向播放"), keys: "J / K / L"},
        {category: qsTr("播放与定位"), action: qsTr("上一帧 / 下一帧"), keys: "← / →"},
        {category: qsTr("播放与定位"), action: qsTr("跳到开头 / 结尾"), keys: "Home / End"},
        {category: qsTr("播放与定位"), action: qsTr("切换全屏"), keys: "F11"},
        {category: qsTr("时间线编辑"), action: qsTr("分割所选片段"), keys: "Ctrl+K / Ctrl+B"},
        {category: qsTr("时间线编辑"), action: qsTr("复制所选片段"), keys: "Ctrl+D"},
        {category: qsTr("时间线编辑"), action: qsTr("删除 / 波纹删除"), keys: "Delete / Shift+Delete"},
        {category: qsTr("时间线编辑"), action: qsTr("撤销 / 重做"), keys: "Ctrl+Z / Ctrl+Y / Ctrl+Shift+Z"},
        {category: qsTr("时间线编辑"), action: qsTr("全选 / 清除选择"), keys: "Ctrl+A / Ctrl+Shift+A"},
        {category: qsTr("时间线编辑"), action: qsTr("设置序列入点 / 出点"), keys: "I / O"},
        {category: qsTr("时间线编辑"), action: qsTr("清除序列入出点"), keys: "Ctrl+Shift+X"},
        {category: qsTr("时间线编辑"), action: qsTr("添加时间线标记"), keys: "M"},
        {category: qsTr("时间线编辑"), action: qsTr("切换吸附"), keys: "S"},
        {category: qsTr("时间线编辑"), action: qsTr("放大 / 缩小时间线"), keys: "= / -"},
        {category: qsTr("时间线编辑"), action: qsTr("适配整个时间线"), keys: "\\"},
        {category: qsTr("项目与面板"), action: qsTr("导入素材"), keys: "Ctrl+I"},
        {category: qsTr("项目与面板"), action: qsTr("打开导出页"), keys: "Ctrl+M"},
        {category: qsTr("项目与面板"), action: qsTr("标准 / 媒体 / 竖屏布局"), keys: "Ctrl+Alt+1 / 2 / 3"},
        {category: qsTr("项目与面板"), action: qsTr("最大化播放器 / 时间线"), keys: "Ctrl+Alt+P / T"},
        {category: qsTr("项目与面板"), action: qsTr("退出面板最大化 / 清除选择"), keys: "Ctrl+Alt+0 / Esc"},
        {category: qsTr("项目与面板"), action: qsTr("打开快捷键总览"), keys: "Ctrl+/"}
    ]
    readonly property var filteredRows: {
        const query = shortcutSearch.text.trim().toLocaleLowerCase()
        if (query.length === 0)
            return shortcutRows
        return shortcutRows.filter(function(item) {
            return String(item.category + " " + item.action + " " + item.keys)
                .toLocaleLowerCase().indexOf(query) >= 0
        })
    }

    contentItem: ColumnLayout {
        width: root.availableWidth
        height: root.availableHeight
        spacing: 10

        AppTextField {
            id: shortcutSearch
            objectName: "shortcutSearchField"
            Layout.fillWidth: true
            placeholderText: qsTr("搜索操作或按键")
        }

        ListView {
            id: shortcutList
            objectName: "shortcutReferenceList"
            Layout.fillWidth: true
            Layout.fillHeight: true
            model: root.filteredRows
            clip: true
            spacing: 4
            section.property: "category"
            section.delegate: Text {
                required property string section
                width: shortcutList.width
                height: 34
                verticalAlignment: Text.AlignVCenter
                text: section
                color: Theme.accentHover
                font.pixelSize: Theme.fontSizeBody
                font.weight: Font.DemiBold
            }
            delegate: Rectangle {
                required property int index
                required property var modelData
                width: shortcutList.width
                height: 38
                radius: Theme.radiusSmall
                color: index % 2 === 0 ? Theme.surfaceRaised : Theme.transparent
                RowLayout {
                    anchors.fill: parent
                    anchors.leftMargin: 10
                    anchors.rightMargin: 10
                    Text {
                        Layout.fillWidth: true
                        text: modelData.action
                        color: Theme.text
                        elide: Text.ElideRight
                    }
                    Text {
                        text: modelData.keys
                        color: Theme.textMuted
                        font.family: Theme.monoFontFamily
                        font.pixelSize: Theme.fontSizeCaption
                    }
                }
            }
        }

        RowLayout {
            Layout.fillWidth: true
            Text {
                Layout.fillWidth: true
                text: qsTr("输入框和网页交互获得焦点时，单键快捷键会暂停响应。")
                color: Theme.textMuted
                font.pixelSize: Theme.fontSizeCaption
            }
            AppButton {
                text: qsTr("关闭")
                onClicked: root.close()
            }
        }
    }
}

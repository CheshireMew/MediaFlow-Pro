import QtQuick
import QtQuick.Controls
import ".."

TextField {
    property string collaborationPath: ""
    property var sourceText: undefined
    property var draftTexts: ({})
    property bool applyingSourceText: false

    function restoreSourceText() {
        if (sourceText === undefined)
            return;
        applyingSourceText = true;
        const hasDraft = collaborationPath.length > 0
            && mediaflow.workspaceProjectController.hasCollaborationDraft(collaborationPath);
        text = hasDraft && draftTexts[collaborationPath] !== undefined
            ? draftTexts[collaborationPath] : String(sourceText);
        applyingSourceText = false;
    }

    function rememberDraft() {
        if (applyingSourceText || collaborationPath.length === 0)
            return;
        draftTexts[collaborationPath] = text;
        mediaflow.workspaceProjectController.beginCollaborationDraft(collaborationPath);
    }

    onSourceTextChanged: restoreSourceText()
    onCollaborationPathChanged: Qt.callLater(restoreSourceText)
    Component.onCompleted: restoreSourceText()
    Connections {
        target: control
        function onTextEdited() {
            control.rememberDraft();
        }
    }
    id: control
    property bool error: false
    hoverEnabled: true
    focusPolicy: Qt.StrongFocus
    implicitHeight: Theme.controlHeight
    leftPadding: 12
    rightPadding: 12
    topPadding: 7
    bottomPadding: 7
    selectByMouse: true
    color: control.enabled ? Theme.text : Theme.textDisabled
    placeholderTextColor: control.enabled ? Theme.textMuted : Theme.textDisabled
    selectionColor: Theme.accent
    selectedTextColor: Theme.onAccent
    font.pixelSize: Theme.fontSizeBody
    Accessible.name: placeholderText
    background: Rectangle {
        radius: Theme.radiusSmall
        color: control.enabled
            ? control.readOnly ? Theme.fieldReadOnly : Theme.field
            : Theme.controlDisabled
        border.color: control.error
            ? Theme.danger
            : control.activeFocus
            ? Theme.focusColor
            : control.hovered
            ? Theme.borderStrong
            : Theme.borderSubtle
        border.width: control.activeFocus ? 2 : 1

        Behavior on color {
            ColorAnimation { duration: Theme.durationFast }
        }
        Behavior on border.color {
            ColorAnimation { duration: Theme.durationFast }
        }
    }
}

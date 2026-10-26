/*!
    SaveDialog.qml
    --------------
    Lazily loaded Save As dialog for edited images.
*/

import QtQuick
import QtQuick.Dialogs
import "DialogRuntime.js" as DialogRuntime

FileDialog {
    id: dialog
    objectName: "saveDialog"

    required property var formatSupport
    required property url suggestedFile

    signal fileChosen(url fileUrl)
    signal openFailed(string message)

    function openSafely() {
        return DialogRuntime.open(dialog)
    }

    title: "Save edited image as"
    fileMode: FileDialog.SaveFile
    nameFilters: formatSupport.saveNameFilters
    defaultSuffix: "png"

    Component.onCompleted: selectedFile = suggestedFile
    onAccepted: fileChosen(selectedFile)
}

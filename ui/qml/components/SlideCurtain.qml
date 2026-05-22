import QtQuick

Rectangle {
    id: root

    // Public properties — set from Python before slideIn/slideOut
    property int slideDuration: 350
    property int direction: 1       // 1 = from right, -1 = from left
    property color curtainColor: "#ffffff"
    property int curtainWidth: 0    // 0 means full parent width
    property string panelId: ""     // "config" or "search"

    x: direction > 0 ? parent.width : -effectiveWidth
    y: 0
    width: effectiveWidth
    height: parent.height
    color: curtainColor

    readonly property int effectiveWidth: curtainWidth > 0 ? curtainWidth : parent.width

    function slideIn() {
        slideAnim.from = direction > 0 ? parent.width : -effectiveWidth
        slideAnim.to = 0
        slideAnim.start()
    }

    function slideOut() {
        slideAnim.from = 0
        slideAnim.to = direction > 0 ? parent.width : -effectiveWidth
        slideAnim.start()
    }

    NumberAnimation {
        id: slideAnim
        target: root
        property: "x"
        duration: root.slideDuration
        easing.type: Easing.InOutExpo

        onFinished: {
            var showing = root.x === 0
            if (typeof pyBridge !== "undefined" && root.panelId !== "") {
                pyBridge.onSlideComplete(root.panelId, showing)
            }
        }
    }
}

pragma Singleton
import QtQuick

QtObject {
    // Standardized durations (ms)
    readonly property int slideDuration: 350
    readonly property int fadeFast: 150
    readonly property int fadeMedium: 300
    readonly property int fadeSlow: 1200

    // Standardized easing curves
    property var slideEasing: Easing.InOutExpo
    property var fadeOutEasing: Easing.InOutExpo
    property var smoothEasing: Easing.InOutExpo
}

import QtQuick
import "../components"

SlideCurtain {
    direction: -1       // slide from left
    curtainWidth: 360   // matches GlobalSearchWidget.sizeHint().width()
    panelId: "search"
}

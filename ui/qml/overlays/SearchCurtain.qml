import QtQuick
import "../components"

SlideCurtain {
    direction: -1       // slide from left
    curtainWidth: 300   // matches GlobalSearchWidget.sizeHint().width()
    panelId: "search"
}

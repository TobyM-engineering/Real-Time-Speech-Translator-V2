// Translator V2 — Signal direction prototype (chosen 2026-08-26).
// Interactive: tap a half to cycle its state, tap the pending band to cancel,
// tap the mute circle to toggle mute. Sizes are the real committed spec:
// readable text >= 48 px, status word ~92 px fit-to-width, cancel band 160 px,
// touch targets >= 120 px. Runs fullscreen on the 720x1280 panel.
import QtQuick
import QtQuick.Window

Window {
    id: win
    visible: true
    visibility: Window.FullScreen
    color: "black"
    title: "Translator V2"

    component PersonHalf: Rectangle {
        id: half
        property string lang: "English"
        property string flag: "🇬🇧"
        property int stateIdx: 0    // 0 idle, 1 listening, 2 translating, 3 speaking
        property bool muted: false
        property bool picking: false
        readonly property var stateNames: ["READY", "LISTENING", "TRANSLATING", "SPEAKING"]
        readonly property var stateColors: ["#101216", "#0B4030", "#413306", "#0E2F52"]
        readonly property var ringColors: ["#3A3D42", "#2EE6A8", "#F5C542", "#5AB0FF"]

        color: muted ? "#2A0B0F" : stateColors[stateIdx]
        Behavior on color { ColorAnimation { duration: 350 } }

        MouseArea {
            anchors.fill: parent
            onClicked: if (!half.muted) half.stateIdx = (half.stateIdx + 1) % 4
        }

        Rectangle {  // language chip
            x: 20; y: 20; height: 80; width: chipText.width + 56
            radius: 16; color: "#26FFFFFF"
            Text {
                id: chipText; anchors.centerIn: parent
                text: half.flag + "  " + half.lang
                color: "white"; font.pixelSize: 32; font.bold: true
            }
            MouseArea { anchors.fill: parent; onClicked: half.picking = true }
        }

        Rectangle {  // mute toggle
            width: 120; height: 120; radius: 60
            color: half.muted ? "#66FF5A6E" : "#26FFFFFF"
            anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 16
            Text { anchors.centerIn: parent; text: "🔇"; font.pixelSize: 52 }
            MouseArea { anchors.fill: parent; onClicked: half.muted = !half.muted }
        }

        Rectangle {  // state ring — yields entirely to the band while translating
            id: ring
            visible: half.stateIdx !== 2
            width: 180; height: 180; radius: 90
            color: "transparent"; border.width: 11
            border.color: half.muted ? "#FF5A6E" : half.ringColors[half.stateIdx]
            anchors.centerIn: parent; anchors.verticalCenterOffset: -70
            opacity: (half.stateIdx === 0 && !half.muted) ? 0.25 : 0.95
            SequentialAnimation on scale {
                running: !half.muted && (half.stateIdx === 1 || half.stateIdx === 3)
                loops: Animation.Infinite
                NumberAnimation { from: 1.0; to: 1.12; duration: 600; easing.type: Easing.InOutQuad }
                NumberAnimation { from: 1.12; to: 1.0; duration: 600; easing.type: Easing.InOutQuad }
            }
            RotationAnimation on rotation {
                running: !half.muted && half.stateIdx === 2
                loops: Animation.Infinite; from: 0; to: 360; duration: 2400
            }
        }

        Text {  // status word, per-language fit-to-width.
                // While translating it shrinks and moves up top so the band owns the lower half.
            text: half.muted ? "MUTED" : half.stateNames[half.stateIdx]
            color: "white"; font.bold: true
            font.pixelSize: half.stateIdx === 2 && !half.muted ? 64 : 92
            fontSizeMode: Text.HorizontalFit; minimumPixelSize: 48
            width: parent.width - 80
            horizontalAlignment: Text.AlignHCenter
            anchors.centerIn: parent
            anchors.verticalCenterOffset: half.stateIdx === 2 && !half.muted
                                          ? -parent.height / 2 + 150 : 78
            opacity: (half.stateIdx === 0 && !half.muted) ? 0.4 : 1.0
        }

        Rectangle {  // pending band: the tappable cancel. Grows with the transcript —
                     // the full sentence must be readable, or the feature is pointless.
            id: band
            visible: !half.muted && half.stateIdx === 2
            property string heardText: "“I went to the store and bought some milk, and then stopped to chat with the neighbour about the weekend.”"
            height: Math.min(Math.max(160, measure.paintedHeight + 88), parent.height - 300)
            clip: true
            radius: 16
            color: "#61000000"; border.color: "#48FFFFFF"; border.width: 2
            anchors { left: parent.left; right: parent.right; bottom: parent.bottom; margins: 16 }
            property real drainT: 1.0
            NumberAnimation on drainT {
                running: band.visible; from: 1.0; to: 0.0
                duration: 3600; loops: Animation.Infinite
            }
            Text {
                id: xmark
                text: "✕"; color: "white"; font.pixelSize: 48
                anchors.left: parent.left; anchors.leftMargin: 28
                anchors.verticalCenter: parent.verticalCenter
            }
            Text {  // invisible measurer: transcript height at full 48 px drives band growth
                id: measure
                visible: false
                width: bandCol.width
                text: band.heardText
                font.pixelSize: 48; wrapMode: Text.Wrap; lineHeight: 1.15
            }
            Column {
                id: bandCol
                spacing: 6
                anchors.left: xmark.right; anchors.leftMargin: 24
                anchors.right: parent.right; anchors.rightMargin: 28
                anchors.top: parent.top; anchors.topMargin: 20
                Text { text: "heard — tap to cancel"; color: "#99FFFFFF"; font.pixelSize: 24 }
                Text {  // full transcript: grows the band; past the cap it shrinks, never clips
                    width: parent.width
                    height: band.height - 84
                    text: band.heardText
                    color: "white"; font.pixelSize: 48
                    wrapMode: Text.Wrap; lineHeight: 1.15
                    fontSizeMode: Text.Fit; minimumPixelSize: 36
                    verticalAlignment: Text.AlignTop
                }
            }
            Rectangle {
                height: 6; radius: 3; color: "#F5C542"
                anchors.bottom: parent.bottom; anchors.left: parent.left
                anchors.margins: 2
                width: (band.width - 4) * band.drainT
            }
            MouseArea {
                anchors.fill: parent
                onClicked: { half.stateIdx = 0; flash.opacity = 1.0 }
            }
        }

        Rectangle {  // cancel confirmation flash
            id: flash
            anchors.fill: parent; color: "#3B0E12"; opacity: 0
            visible: opacity > 0
            Text { anchors.centerIn: parent; text: "CANCELLED"
                   color: "#FF5A6E"; font.pixelSize: 84; font.bold: true }
            Behavior on opacity { NumberAnimation { duration: 200 } }
            Timer { running: flash.opacity > 0.5; interval: 900; onTriggered: flash.opacity = 0 }
            MouseArea { anchors.fill: parent }  // swallow taps during flash
        }

        Rectangle {  // language picker: one flat two-column grid, best accuracy first.
                     // No tiers — the color-coded number on each cell is the trust signal;
                     // ✓ marks languages actually measured on this device.
            id: picker
            visible: half.picking
            anchors.fill: parent
            color: "#FA0E1013"
            GridView {
                id: langGrid
                anchors.fill: parent
                anchors.margins: 10
                clip: true
                cellWidth: width / 2
                cellHeight: 122
                model: langCatalog
                delegate: Item {
                    required property var modelData
                    width: langGrid.cellWidth; height: langGrid.cellHeight
                    Rectangle {
                        anchors.fill: parent; anchors.margins: 5
                        radius: 12
                        color: modelData.name === half.lang ? "#0F6B5C" : "#14171B"
                        border.color: modelData.name === half.lang ? "#15866F" : "#22262C"
                        border.width: 2
                        Row {
                            anchors.left: parent.left; anchors.leftMargin: 16
                            anchors.right: parent.right; anchors.rightMargin: 12
                            anchors.top: parent.top; anchors.topMargin: 16
                            spacing: 10
                            Text { text: modelData.flag; font.pixelSize: 30 }
                            Text {
                                text: modelData.name
                                color: "white"; font.pixelSize: 26; font.bold: true
                                width: langGrid.cellWidth - (modelData.verified ? 130 : 100)
                                fontSizeMode: Text.HorizontalFit; minimumPixelSize: 17
                                elide: Text.ElideRight
                                // RTL scripts (ar/he/fa/ur) naturally align right inside
                                // an explicit-width Text, drifting away from the flag.
                                // Pin the box's alignment; the glyphs still run RTL.
                                horizontalAlignment: Text.AlignLeft
                            }
                            Text { visible: modelData.verified; text: "✓"
                                   color: "#2EE6A8"; font.pixelSize: 20 }
                        }
                        Text {  // trust marker: estimated word error rate
                            text: "~" + modelData.wer + "%"
                            color: modelData.wer <= 12 ? "#2EE6A8"
                                 : modelData.wer <= 25 ? "#F5C542" : "#FF5A6E"
                            font.pixelSize: 22; font.bold: modelData.wer > 25
                            anchors.right: parent.right; anchors.rightMargin: 14
                            anchors.bottom: parent.bottom; anchors.bottomMargin: 8
                        }
                        MouseArea {
                            anchors.fill: parent
                            onClicked: {
                                half.flag = modelData.flag
                                half.lang = modelData.name
                                half.picking = false
                            }
                        }
                    }
                }
            }
        }
    }

    Column {
        anchors.fill: parent
        PersonHalf {
            width: win.width; height: (win.height - 12) / 2
            rotation: 180
            lang: "Español"; flag: "🇪🇸"; stateIdx: 0
        }
        Rectangle {  // neutral center strip
            width: win.width; height: 12; color: "black"
            Rectangle { width: 8; height: 8; radius: 4; color: "#2EE6A8"; anchors.centerIn: parent }
        }
        PersonHalf {
            width: win.width; height: (win.height - 12) / 2
            lang: "English"; flag: "🇬🇧"; stateIdx: 2; picking: true
        }
    }
}

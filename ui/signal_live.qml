// Translator V2 — Signal UI, stage 2: driven by the live pipeline bridge.
// Real states, real transcripts, working cancel/mute/picker. Status words come
// from the catalog's curated localizations for each person's language.
import QtQuick
import QtQuick.Window

Window {
    id: win
    visible: true
    visibility: Window.FullScreen
    color: "black"
    title: "Translator V2"

    property bool pipelineReady: false
    property string faultMsg: ""
    property var levelData: null
    Connections {
        target: bridge
        function onReady() { win.pipelineReady = true }
        function onFaultChanged(msg) { win.faultMsg = msg }
        function onLevelUpdate(j) { win.levelData = JSON.parse(j) }
    }

    component PersonHalf: Rectangle {
        id: half
        property string person: "A"
        property var langEntry: bridge.getLang(person)
        property string stateKey: "ready"
        property string transcript: ""
        property bool draining: false
        property bool localMuted: false
        property real hintUntil: 0

        property real backlogS: 0
        property bool holdActive: false
        readonly property var stateColors: ({
            ready: "#101216", listening: "#0B4030",
            translating: "#413306", muted: "#2A0B0F",
            speaking: "#0E2F52", pause_soft: "#4A3A06", pause_hard: "#5A1010"
        })
        readonly property var ringColors: ({
            ready: "#3A3D42", listening: "#2EE6A8",
            translating: "#F5C542", muted: "#FF5A6E",
            speaking: "#5AB0FF", pause_soft: "#F5C542", pause_hard: "#FF5A6E"
        })
        function word(key) {
            var s = (langEntry && langEntry.ui && langEntry.ui[key])
                    ? langEntry.ui[key] : key
            // pause states carry localized sentences, not status words
            return key.indexOf("pause") === 0 ? s : s.toUpperCase()
        }

        color: holdActive ? "#0E5040" : (stateColors[stateKey] || "#101216")
        Behavior on color { ColorAnimation { duration: 350 } }

        MouseArea {  // hold-to-talk: press-and-hold anywhere on the half's
                     // background. Declared FIRST so every real control —
                     // chip, mute, band, picker, flash — stacks above it and
                     // keeps its own tap. A short hold arms nothing.
            id: pttArea
            anchors.fill: parent
            enabled: half.stateKey !== "muted"
            function endHold() {
                pttTimer.stop()
                charge.visible = false
                if (half.holdActive) {
                    half.holdActive = false
                    bridge.setHold(half.person, false)
                }
            }
            onPressed: {
                charge.visible = true
                chargeAnim.restart()
                pttTimer.restart()
            }
            onReleased: endHold()
            onCanceled: endHold()
            Timer {
                id: pttTimer; interval: 1000
                onTriggered: if (pttArea.pressed
                                 && half.stateKey !== "muted") {
                    charge.visible = false
                    half.holdActive = true
                    bridge.setHold(half.person, true)
                }
            }
        }

        Connections {
            target: bridge
            function onStateChanged(p, s) {
                if (p === half.person) half.stateKey = s
            }
            function onGroupText(p, text) {
                if (p !== half.person) return
                half.transcript = text
                half.draining = false
                drain.stop()
                band.drainT = 1.0
            }
            function onGroupClosed(p, cancelled) {
                if (p !== half.person) return
                if (cancelled) {
                    half.transcript = ""
                    half.draining = false
                    flash.opacity = 1.0
                } else if (half.transcript !== "") {
                    half.draining = true
                    drain.restart()
                }
            }
            function onLangChanged(p, entry) {
                if (p === half.person) half.langEntry = entry
            }
            function onOverlapHint() { half.hintUntil = Date.now() + 2500 }
            function onBacklogMeter(p, s) {
                if (p === half.person) half.backlogS = s
            }
        }

        Rectangle {  // language chip
            x: 20; y: 20; height: 80; width: chipText.width + 56
            radius: 16; color: "#26FFFFFF"
            Text {
                id: chipText; anchors.centerIn: parent
                text: (half.langEntry ? half.langEntry.flag + "  "
                       + half.langEntry.name : "…")
                color: "white"; font.pixelSize: 32; font.bold: true
            }
            MouseArea { anchors.fill: parent; onClicked: picker.visible = true }
        }

        Rectangle {  // mute toggle
            width: 120; height: 120; radius: 60
            color: half.stateKey === "muted" ? "#66FF5A6E" : "#26FFFFFF"
            anchors.right: parent.right; anchors.top: parent.top
            anchors.margins: 16
            Text { anchors.centerIn: parent; text: "🔇"; font.pixelSize: 52 }
            MouseArea {
                anchors.fill: parent
                onClicked: {
                    half.localMuted = !half.localMuted
                    bridge.setMuted(half.person, half.localMuted)
                }
            }
        }

        Rectangle {  // state ring — smaller and higher so the status word
                     // never collides with it; yields entirely to the band
            visible: half.transcript === "" && half.stateKey.indexOf("pause") !== 0
                     && !half.holdActive
            width: 150; height: 150; radius: 75
            color: "transparent"; border.width: 11
            border.color: ringColors[half.stateKey] || "#3A3D42"
            anchors.centerIn: parent; anchors.verticalCenterOffset: -85
            opacity: half.stateKey === "ready" ? 0.25 : 0.95
            SequentialAnimation on scale {
                running: half.stateKey === "listening"
                loops: Animation.Infinite
                NumberAnimation { from: 1.0; to: 1.12; duration: 600
                                  easing.type: Easing.InOutQuad }
                NumberAnimation { from: 1.12; to: 1.0; duration: 600
                                  easing.type: Easing.InOutQuad }
            }
        }

        Rectangle {  // hold-to-talk charge cue: grows through the 1 s press,
                     // vanishes without trace if released early
            id: charge
            visible: false
            width: 110; height: 110; radius: 55
            color: "transparent"; border.width: 6; border.color: "#882EE6A8"
            anchors.centerIn: parent; anchors.verticalCenterOffset: -85
            NumberAnimation { id: chargeAnim; target: charge
                              property: "scale"
                              from: 0.5; to: 1.45; duration: 1000 }
        }
        Rectangle {  // hold-to-talk active: filled pulsing dot replaces the ring
            visible: half.holdActive && half.transcript === ""
            width: 150; height: 150; radius: 75; color: "#2EE6A8"
            anchors.centerIn: parent; anchors.verticalCenterOffset: -85
            SequentialAnimation on opacity {
                running: half.holdActive
                loops: Animation.Infinite
                NumberAnimation { from: 1.0; to: 0.55; duration: 450 }
                NumberAnimation { from: 0.55; to: 1.0; duration: 450 }
            }
        }
        Rectangle {  // hold-to-talk active: unmistakable frame on this half
            visible: half.holdActive
            anchors.fill: parent
            color: "transparent"; radius: 4
            border.width: 10; border.color: "#2EE6A8"
        }

        Text {  // status word (or localized pause sentence), fit-to-width
            text: win.pipelineReady ? half.word(half.stateKey) : "…"
            color: "white"; font.bold: true
            font.pixelSize: (half.transcript !== ""
                             || half.stateKey.indexOf("pause") === 0) ? 64 : 92
            fontSizeMode: Text.HorizontalFit; minimumPixelSize: 40
            wrapMode: Text.Wrap
            width: parent.width - 80
            horizontalAlignment: Text.AlignHCenter
            anchors.horizontalCenter: parent.horizontalCenter
            // pause phrases get their own zone below the chips (y 156+),
            // never overlapping the language label or the mute button
            y: half.stateKey.indexOf("pause") === 0 ? 156
               : half.transcript !== "" ? Math.max(20, 150 - height / 2)
               : parent.height / 2 + 100 - height / 2
            opacity: half.stateKey === "ready" ? 0.4 : 1.0
        }

        Text {  // overlap hint, localized (D7)
            visible: half.hintUntil > Date.now()
            text: half.langEntry && half.langEntry.ui
                  ? half.langEntry.ui.one_at_a_time : "One at a time, please."
            color: "#F5C542"; font.pixelSize: 30; font.bold: true
            anchors.horizontalCenter: parent.horizontalCenter
            anchors.bottom: parent.bottom; anchors.bottomMargin: 24
            Timer { interval: 300; running: half.hintUntil > 0; repeat: true
                    onTriggered: if (Date.now() > half.hintUntil) half.hintUntil = 0 }
        }

        Rectangle {  // pending band: the transcript is primary content in the
                     // cancel window — grow upward, shrink 48→36 px, then
                     // SCROLL. It is never clipped.
            id: band
            visible: half.transcript !== ""
            property real drainT: 1.0
            readonly property real maxBand: parent.height
                - (half.stateKey.indexOf("pause") === 0 ? 380 : 230)
            readonly property real avail: height - 84
            readonly property bool scrollNeeded: measure36.paintedHeight > avail
            height: Math.min(Math.max(160, measure48.paintedHeight + 88), maxBand)
            radius: 16
            color: "#61000000"; border.color: "#48FFFFFF"; border.width: 2
            anchors { left: parent.left; right: parent.right
                      bottom: parent.bottom; margins: 16 }
            onVisibleChanged: if (visible) { drainT = 1.0; flick.contentY = 0 }
            NumberAnimation {
                id: drain; target: band; property: "drainT"
                from: 1.0; to: 0.0; duration: 5000
                onFinished: if (half.draining) {
                    half.transcript = ""
                    half.draining = false
                }
            }
            Text {
                id: xmark
                text: "✕"; color: "white"; font.pixelSize: 48
                anchors.left: parent.left; anchors.leftMargin: 28
                anchors.verticalCenter: parent.verticalCenter
            }
            Text {  // D5 backlog meter — latched by the bridge: holds its last
                    // value through inter-chunk dips, clears only after real quiet
                visible: half.backlogS > 0
                text: "≈ " + Math.round(half.backlogS) + " s"
                color: half.backlogS >= 15 ? "#FF5A6E" : "#F5C542"
                font.pixelSize: 24; font.bold: half.backlogS >= 15
                anchors.right: parent.right; anchors.rightMargin: 20
                anchors.top: parent.top; anchors.topMargin: 16
            }
            Text { id: measure48; visible: false; textFormat: Text.StyledText; width: bandCol.width
                   text: half.transcript
                   font.pixelSize: 48; wrapMode: Text.Wrap; lineHeight: 1.15 }
            Text { id: measure36; visible: false; textFormat: Text.StyledText; width: bandCol.width
                   text: half.transcript
                   font.pixelSize: 36; wrapMode: Text.Wrap; lineHeight: 1.15 }
            MouseArea {  // cancel via the frame, label, or ✕ region
                anchors.fill: parent
                onClicked: bridge.cancelGroup(half.person)
            }
            Column {
                id: bandCol
                spacing: 6
                anchors.left: xmark.right; anchors.leftMargin: 24
                anchors.right: parent.right; anchors.rightMargin: 28
                anchors.top: parent.top; anchors.topMargin: 20
                Text { text: "heard — tap to cancel"; color: "#99FFFFFF"
                       font.pixelSize: 24 }
                Flickable {
                    id: flick
                    width: parent.width
                    height: band.avail
                    clip: true
                    contentHeight: body.height
                    interactive: band.scrollNeeded
                    Text {
                        id: body; textFormat: Text.StyledText
                        width: flick.width
                        text: half.transcript
                        color: "white"
                        wrapMode: Text.Wrap; lineHeight: 1.15
                        font.pixelSize: band.scrollNeeded ? 36 : 48
                        fontSizeMode: band.scrollNeeded ? Text.FixedSize
                                                        : Text.Fit
                        minimumPixelSize: 36
                        height: band.scrollNeeded ? implicitHeight : flick.height
                        verticalAlignment: Text.AlignTop
                    }
                    MouseArea {  // tap = cancel; drags go to the Flickable
                        anchors.fill: parent
                        onClicked: bridge.cancelGroup(half.person)
                    }
                }
            }
            Text {  // more-below affordance while scrollable
                visible: band.scrollNeeded && !flick.atYEnd
                text: "▼"
                color: "#B3FFFFFF"; font.pixelSize: 22
                anchors.right: parent.right; anchors.rightMargin: 18
                anchors.bottom: parent.bottom; anchors.bottomMargin: 14
            }
            Rectangle {
                height: 6; radius: 3; color: "#F5C542"
                anchors.bottom: parent.bottom; anchors.left: parent.left
                anchors.margins: 2
                width: (band.width - 4) * band.drainT
            }
        }

        Rectangle {  // cancel flash
            id: flash
            anchors.fill: parent; color: "#3B0E12"; opacity: 0
            visible: opacity > 0
            Text { anchors.centerIn: parent
                   text: half.word("cancelled")
                   color: "#FF5A6E"; font.pixelSize: 84; font.bold: true }
            Behavior on opacity { NumberAnimation { duration: 200 } }
            Timer { running: flash.opacity > 0.5; interval: 900
                    onTriggered: flash.opacity = 0 }
            MouseArea { anchors.fill: parent }
        }

        Rectangle {  // language picker: flat accuracy grid (shared design)
            id: picker
            visible: false
            anchors.fill: parent
            color: "#FA0E1013"
            MouseArea {  // tap beside the grid closes the picker — and stops
                         // presses falling through to the hold-to-talk layer
                anchors.fill: parent
                onClicked: picker.visible = false
            }
            GridView {
                id: langGrid
                anchors.fill: parent; anchors.margins: 10
                clip: true
                cellWidth: width / 2; cellHeight: 122
                model: langCatalog
                delegate: Item {
                    required property var modelData
                    width: langGrid.cellWidth; height: langGrid.cellHeight
                    Rectangle {
                        anchors.fill: parent; anchors.margins: 5
                        radius: 12
                        color: half.langEntry && modelData.code === half.langEntry.code
                               ? "#0F6B5C" : "#14171B"
                        border.color: half.langEntry && modelData.code === half.langEntry.code
                                      ? "#15866F" : "#22262C"
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
                                horizontalAlignment: Text.AlignLeft
                            }
                            Text { visible: modelData.verified; text: "✓"
                                   color: "#2EE6A8"; font.pixelSize: 20 }
                        }
                        Text {
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
                                bridge.setLanguage(half.person, modelData.code)
                                picker.visible = false
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
            person: "B"
        }
        Rectangle {  // centre strip — its dot doubles as the mic-check button
                     // (tap target lives at window level, below)
            width: win.width; height: 12; color: "black"
            Rectangle { width: 14; height: 14; radius: 7
                        color: win.pipelineReady ? "#2EE6A8" : "#F5C542"
                        anchors.centerIn: parent }
        }
        PersonHalf {
            width: win.width; height: (win.height - 12) / 2
            person: "A"
        }
    }

    MouseArea {  // the centre dot's tap target — at window level so it wins
                 // over both halves' hold-to-talk layers. Chips and mute
                 // buttons stay ≥150 px away at centre-x; both transcript
                 // bands live at the OUTER screen edges.
        width: 150; height: 110
        anchors.centerIn: parent
        onClicked: levelPanel.visible = true
    }

    component LevelMeter: Item {
        id: meter
        property string label: ""
        property var d: null          // {inst, level, floor, gap} or null
        height: 140
        // bar maps -60 dBFS (left) .. 0 dBFS (right)
        function pos(v) { return Math.max(0, Math.min(1, (v + 60) / 60)) }
        Text { text: meter.label; color: "#C7CBD1"
               font.pixelSize: 24; font.bold: true }
        Text {
            text: meter.d ? ("speech " + meter.d.level.toFixed(1)
                    + "   room " + meter.d.floor.toFixed(1)
                    + "   gap " + meter.d.gap.toFixed(0) + " dB") : "…"
            color: "white"; font.pixelSize: 24
            anchors.right: parent.right
        }
        Rectangle {
            id: track
            y: 40; width: parent.width; height: 48; radius: 8
            color: "#1A1D22"; border.color: "#2A2E34"; border.width: 1
            Rectangle {  // live fill (~130 ms RMS)
                width: track.width * meter.pos(meter.d ? meter.d.inst : -120)
                height: parent.height; radius: 8; color: "#8CFFFFFF"
                Behavior on width { NumberAnimation { duration: 90 } }
            }
            Rectangle {  // verified good band (levels.py BAND -26..-21)
                x: track.width * meter.pos(-26)
                width: track.width * (meter.pos(-21) - meter.pos(-26))
                height: parent.height; color: "#382EE6A8"
            }
            Rectangle {  // too-hot zone (levels.py HOT -15)
                x: track.width * meter.pos(-15)
                width: track.width * (1 - meter.pos(-15))
                height: parent.height; color: "#26FF5A6E"
            }
            Rectangle {  // speech level, 90th percentile of last 5 s
                visible: meter.d !== null
                x: track.width * meter.pos(meter.d ? meter.d.level : -120) - 2
                width: 4; height: parent.height; color: "white"
            }
            Rectangle {  // background level, 10th percentile
                visible: meter.d !== null
                x: track.width * meter.pos(meter.d ? meter.d.floor : -120) - 2
                width: 4; height: parent.height; color: "#7A828C"
            }
        }
        Text { text: "-60"; color: "#6A7078"; font.pixelSize: 18
               anchors.top: track.bottom; anchors.topMargin: 4; x: 0 }
        Text { text: "-40"; color: "#6A7078"; font.pixelSize: 18
               anchors.top: track.bottom; anchors.topMargin: 4
               x: track.width / 3 - width / 2 }
        Text { text: "-20"; color: "#6A7078"; font.pixelSize: 18
               anchors.top: track.bottom; anchors.topMargin: 4
               x: track.width * 2 / 3 - width / 2 }
        Text { text: "0 dBFS"; color: "#6A7078"; font.pixelSize: 18
               anchors.top: track.bottom; anchors.topMargin: 4
               x: track.width - width }
    }

    Rectangle {  // mic level panel — opened by the centre dot, closed by one
                 // tap anywhere on it. Reads from A's side (a bench tool).
        id: levelPanel
        visible: false
        anchors.centerIn: parent
        width: win.width - 56; height: 680
        radius: 20; color: "#F70D0F13"
        border.color: "#48FFFFFF"; border.width: 2
        onVisibleChanged: {
            win.levelData = null
            bridge.setLevelPanel(visible)
        }
        Column {
            anchors.fill: parent; anchors.margins: 28
            spacing: 18
            Item {
                width: parent.width; height: 36
                Text { text: "MIC CHECK"; color: "white"
                       font.pixelSize: 28; font.bold: true }
                Text { text: "tap anywhere to close  ✕"; color: "#99FFFFFF"
                       font.pixelSize: 24; anchors.right: parent.right }
            }
            LevelMeter { width: parent.width; label: "TX1 · black · A"
                         d: win.levelData ? win.levelData.a : null }
            LevelMeter { width: parent.width; label: "TX2 · grey · B"
                         d: win.levelData ? win.levelData.b : null }
            Text {
                text: "green = verified good band (−26…−21 dBFS) · red = too hot"
                color: "#8A9099"; font.pixelSize: 20
            }
            Text {  // verdict from the louder mic — shared logic in levels.py
                text: win.levelData ? win.levelData.headline : "…"
                font.pixelSize: 54; font.bold: true
                color: !win.levelData ? "#8A9099"
                     : win.levelData.state === "good" ? "#2EE6A8"
                     : win.levelData.state === "waiting" ? "#8A9099"
                     : (win.levelData.state === "quiet"
                        || win.levelData.state === "noisy") ? "#F5C542"
                     : "#FF5A6E"
            }
            Text {
                text: win.levelData ? win.levelData.advice : ""
                color: "#D0D4D8"; font.pixelSize: 26
                wrapMode: Text.Wrap; width: parent.width
            }
        }
        MouseArea { anchors.fill: parent; onClicked: levelPanel.visible = false }
    }

    Rectangle {  // device fault pill — overlays the center strip, both readers
        visible: win.faultMsg !== ""
        anchors.centerIn: parent
        width: Math.min(win.width - 40, fcol.width + 48); height: 72
        radius: 14; color: "#E5300A10"; border.color: "#FF5A6E"; border.width: 2
        Column {
            id: fcol
            anchors.centerIn: parent; spacing: 2
            Text { text: win.faultMsg; color: "#FFB4BC"; font.pixelSize: 20
                   font.bold: true; rotation: 180
                   anchors.horizontalCenter: parent.horizontalCenter }
            Text { text: win.faultMsg; color: "#FFB4BC"; font.pixelSize: 20
                   font.bold: true
                   anchors.horizontalCenter: parent.horizontalCenter }
        }
    }
}

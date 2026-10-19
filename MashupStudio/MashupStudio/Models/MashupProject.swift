import Foundation
import Combine
import SwiftUI

class MashupProject: ObservableObject {
    @Published var trackA: AudioTrack
    @Published var trackB: AudioTrack
    @Published var isPlaying: Bool = false
    @Published var currentTime: TimeInterval = 0
    @Published var zoomLevel: Double = 1.0
    @Published var loopEnabled: Bool = false
    @Published var loopStart: TimeInterval = 0
    @Published var loopEnd: TimeInterval = 30
    @Published var crossfadeDuration: TimeInterval = 2.0
    @Published var masterVolume: Float = 1.0
    @Published var scrollOffset: CGFloat = 0

    private var cancellables = Set<AnyCancellable>()

    var totalDuration: TimeInterval {
        let endA = trackA.isLoaded ? trackA.timelineEnd : 0
        let endB = trackB.isLoaded ? trackB.timelineEnd : 0
        return max(endA, endB, 30)
    }

    var pixelsPerSecond: Double {
        20.0 * zoomLevel
    }

    init() {
        trackA = AudioTrack(label: "A")
        trackB = AudioTrack(label: "B")

        trackA.objectWillChange.sink { [weak self] _ in
            self?.objectWillChange.send()
        }.store(in: &cancellables)

        trackB.objectWillChange.sink { [weak self] _ in
            self?.objectWillChange.send()
        }.store(in: &cancellables)
    }

    func timeToX(_ time: TimeInterval) -> CGFloat {
        CGFloat(time * pixelsPerSecond)
    }

    func xToTime(_ x: CGFloat) -> TimeInterval {
        Double(x) / pixelsPerSecond
    }

    // MARK: - Undo helpers

    func setOffset(_ track: AudioTrack, to value: TimeInterval, undoManager: UndoManager?) {
        let old = track.offset
        undoManager?.registerUndo(withTarget: self) { proj in
            proj.setOffset(track, to: old, undoManager: undoManager)
        }
        undoManager?.setActionName("Move Track \(track.label)")
        track.offset = max(0, value)
    }

    func setTrimStart(_ track: AudioTrack, to value: TimeInterval, undoManager: UndoManager?) {
        let old = track.trimStart
        undoManager?.registerUndo(withTarget: self) { proj in
            proj.setTrimStart(track, to: old, undoManager: undoManager)
        }
        undoManager?.setActionName("Trim Start")
        track.trimStart = min(max(0, value), track.duration - track.trimEnd - 0.1)
    }

    func setTrimEnd(_ track: AudioTrack, to value: TimeInterval, undoManager: UndoManager?) {
        let old = track.trimEnd
        undoManager?.registerUndo(withTarget: self) { proj in
            proj.setTrimEnd(track, to: old, undoManager: undoManager)
        }
        undoManager?.setActionName("Trim End")
        track.trimEnd = min(max(0, value), track.duration - track.trimStart - 0.1)
    }

    func setVolume(_ track: AudioTrack, to value: Float, undoManager: UndoManager?) {
        let old = track.volume
        undoManager?.registerUndo(withTarget: self) { proj in
            proj.setVolume(track, to: old, undoManager: undoManager)
        }
        undoManager?.setActionName("Volume")
        track.volume = value
    }

    func setEQ(_ track: AudioTrack, band: EQBand, to value: Float, undoManager: UndoManager?) {
        let old: Float
        switch band {
        case .low: old = track.eqLow
        case .mid: old = track.eqMid
        case .high: old = track.eqHigh
        }
        undoManager?.registerUndo(withTarget: self) { proj in
            proj.setEQ(track, band: band, to: old, undoManager: undoManager)
        }
        undoManager?.setActionName("EQ \(band.rawValue)")
        switch band {
        case .low: track.eqLow = value
        case .mid: track.eqMid = value
        case .high: track.eqHigh = value
        }
    }

    func setPlaybackRate(_ track: AudioTrack, to value: Float, undoManager: UndoManager?) {
        let old = track.playbackRate
        undoManager?.registerUndo(withTarget: self) { proj in
            proj.setPlaybackRate(track, to: old, undoManager: undoManager)
        }
        undoManager?.setActionName("Speed")
        track.playbackRate = value
    }

    func toggleMute(_ track: AudioTrack, undoManager: UndoManager?) {
        let old = track.isMuted
        undoManager?.registerUndo(withTarget: self) { proj in
            track.isMuted = old
        }
        undoManager?.setActionName(track.isMuted ? "Unmute" : "Mute")
        track.isMuted.toggle()
    }

    func toggleSolo(_ track: AudioTrack, undoManager: UndoManager?) {
        let old = track.isSolo
        undoManager?.registerUndo(withTarget: self) { proj in
            track.isSolo = old
        }
        undoManager?.setActionName(track.isSolo ? "Unsolo" : "Solo")
        track.isSolo.toggle()
    }
}

enum EQBand: String {
    case low = "Low"
    case mid = "Mid"
    case high = "High"
}

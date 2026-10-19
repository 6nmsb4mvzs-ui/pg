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
        let endA = trackA.timelineEnd
        let endB = trackB.timelineEnd
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

    // MARK: - Clip undo

    func setClipOffset(_ clip: AudioClip, to value: TimeInterval, undoManager: UndoManager?) {
        let old = clip.offset
        undoManager?.registerUndo(withTarget: self) { proj in
            proj.setClipOffset(clip, to: old, undoManager: undoManager)
        }
        undoManager?.setActionName("Move Clip")
        clip.offset = max(-clip.trimStart, value)
    }

    func setClipTrimStart(_ clip: AudioClip, to value: TimeInterval, undoManager: UndoManager?) {
        let old = clip.trimStart
        undoManager?.registerUndo(withTarget: self) { proj in
            proj.setClipTrimStart(clip, to: old, undoManager: undoManager)
        }
        undoManager?.setActionName("Trim Start")
        clip.trimStart = min(max(0, value), clip.sourceDuration - clip.trimEnd - 0.1)
    }

    func setClipTrimEnd(_ clip: AudioClip, to value: TimeInterval, undoManager: UndoManager?) {
        let old = clip.trimEnd
        undoManager?.registerUndo(withTarget: self) { proj in
            proj.setClipTrimEnd(clip, to: old, undoManager: undoManager)
        }
        undoManager?.setActionName("Trim End")
        clip.trimEnd = min(max(0, value), clip.sourceDuration - clip.trimStart - 0.1)
    }

    func splitAtPlayhead(undoManager: UndoManager?) {
        let time = currentTime
        let beforeA = trackA.clips
        let beforeB = trackB.clips

        var didSplit = false
        for track in [trackA, trackB] {
            let toSplit = track.clips.filter { $0.timelineStart < time && time < $0.timelineEnd }
            for clip in toSplit {
                if track.splitClip(id: clip.id, at: time) != nil {
                    didSplit = true
                }
            }
        }

        guard didSplit else { return }

        let afterA = trackA.clips
        let afterB = trackB.clips

        undoManager?.registerUndo(withTarget: self) { proj in
            proj.trackA.replaceClips(with: beforeA)
            proj.trackB.replaceClips(with: beforeB)
            undoManager?.registerUndo(withTarget: proj) { proj2 in
                proj2.trackA.replaceClips(with: afterA)
                proj2.trackB.replaceClips(with: afterB)
            }
            undoManager?.setActionName("Split")
        }
        undoManager?.setActionName("Split")
    }

    // MARK: - Track-level undo

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
        undoManager?.registerUndo(withTarget: self) { _ in
            track.isMuted = old
        }
        undoManager?.setActionName(track.isMuted ? "Unmute" : "Mute")
        track.isMuted.toggle()
    }

    func toggleSolo(_ track: AudioTrack, undoManager: UndoManager?) {
        let old = track.isSolo
        undoManager?.registerUndo(withTarget: self) { _ in
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

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
    @Published var selectedTrack: SelectedTrack = .none

    enum SelectedTrack: Equatable {
        case a, b, none
    }

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

    func setClipFadeIn(_ clip: AudioClip, to value: TimeInterval, undoManager: UndoManager?) {
        let old = clip.fadeIn
        undoManager?.registerUndo(withTarget: self) { proj in
            proj.setClipFadeIn(clip, to: old, undoManager: undoManager)
        }
        undoManager?.setActionName("Fade In")
        clip.fadeIn = min(max(0, value), clip.effectiveDuration - clip.fadeOut)
    }

    func setClipFadeOut(_ clip: AudioClip, to value: TimeInterval, undoManager: UndoManager?) {
        let old = clip.fadeOut
        undoManager?.registerUndo(withTarget: self) { proj in
            proj.setClipFadeOut(clip, to: old, undoManager: undoManager)
        }
        undoManager?.setActionName("Fade Out")
        clip.fadeOut = min(max(0, value), clip.effectiveDuration - clip.fadeIn)
    }

    func splitAtPlayhead(undoManager: UndoManager?) {
        let time = currentTime
        let beforeA = trackA.clips
        let beforeB = trackB.clips

        let tracksToSplit: [AudioTrack]
        switch selectedTrack {
        case .a: tracksToSplit = [trackA]
        case .b: tracksToSplit = [trackB]
        case .none: tracksToSplit = [trackA, trackB]
        }

        var didSplit = false
        for track in tracksToSplit {
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

    func deleteClip(_ clip: AudioClip, from track: AudioTrack, undoManager: UndoManager?) {
        let oldClips = track.clips
        track.removeClip(id: clip.id)
        let newClips = track.clips
        undoManager?.registerUndo(withTarget: self) { proj in
            track.replaceClips(with: oldClips)
            undoManager?.registerUndo(withTarget: proj) { proj2 in
                track.replaceClips(with: newClips)
            }
            undoManager?.setActionName("Delete Clip")
        }
        undoManager?.setActionName("Delete Clip")
    }

    func duplicateClip(_ clip: AudioClip, in track: AudioTrack, undoManager: UndoManager?) {
        let dup = AudioClip(
            sourceURL: clip.sourceURL, sourceName: clip.sourceName,
            sourceDuration: clip.sourceDuration, fullWaveform: clip.fullWaveform,
            bpm: clip.bpm,
            offset: clip.timelineEnd - clip.trimStart,
            trimStart: clip.trimStart, trimEnd: clip.trimEnd,
            fadeIn: clip.fadeIn, fadeOut: clip.fadeOut
        )
        let oldClips = track.clips
        track.addClipDirect(dup)
        let newClips = track.clips
        undoManager?.registerUndo(withTarget: self) { proj in
            track.replaceClips(with: oldClips)
            undoManager?.registerUndo(withTarget: proj) { proj2 in
                track.replaceClips(with: newClips)
            }
            undoManager?.setActionName("Duplicate Clip")
        }
        undoManager?.setActionName("Duplicate Clip")
    }

    func deleteAtPlayhead(undoManager: UndoManager?) {
        let time = currentTime
        let tracksToCheck: [AudioTrack]
        switch selectedTrack {
        case .a: tracksToCheck = [trackA]
        case .b: tracksToCheck = [trackB]
        case .none: tracksToCheck = [trackA, trackB]
        }

        let beforeA = trackA.clips
        let beforeB = trackB.clips
        var didDelete = false

        for track in tracksToCheck {
            let hits = track.clips.filter { $0.timelineStart <= time && time < $0.timelineEnd }
            for clip in hits {
                track.removeClip(id: clip.id)
                didDelete = true
            }
        }

        guard didDelete else { return }

        let afterA = trackA.clips
        let afterB = trackB.clips
        undoManager?.registerUndo(withTarget: self) { proj in
            proj.trackA.replaceClips(with: beforeA)
            proj.trackB.replaceClips(with: beforeB)
            undoManager?.registerUndo(withTarget: proj) { proj2 in
                proj2.trackA.replaceClips(with: afterA)
                proj2.trackB.replaceClips(with: afterB)
            }
            undoManager?.setActionName("Delete")
        }
        undoManager?.setActionName("Delete")
    }

    func duplicateAtPlayhead(undoManager: UndoManager?) {
        let time = currentTime
        let tracksToCheck: [AudioTrack]
        switch selectedTrack {
        case .a: tracksToCheck = [trackA]
        case .b: tracksToCheck = [trackB]
        case .none: tracksToCheck = [trackA, trackB]
        }

        let beforeA = trackA.clips
        let beforeB = trackB.clips
        var didDuplicate = false

        for track in tracksToCheck {
            let hits = track.clips.filter { $0.timelineStart <= time && time < $0.timelineEnd }
            for clip in hits {
                let dup = AudioClip(
                    sourceURL: clip.sourceURL, sourceName: clip.sourceName,
                    sourceDuration: clip.sourceDuration, fullWaveform: clip.fullWaveform,
                    bpm: clip.bpm,
                    offset: clip.timelineEnd - clip.trimStart,
                    trimStart: clip.trimStart, trimEnd: clip.trimEnd,
                    fadeIn: clip.fadeIn, fadeOut: clip.fadeOut
                )
                track.addClipDirect(dup)
                didDuplicate = true
            }
        }

        guard didDuplicate else { return }

        let afterA = trackA.clips
        let afterB = trackB.clips
        undoManager?.registerUndo(withTarget: self) { proj in
            proj.trackA.replaceClips(with: beforeA)
            proj.trackB.replaceClips(with: beforeB)
            undoManager?.registerUndo(withTarget: proj) { proj2 in
                proj2.trackA.replaceClips(with: afterA)
                proj2.trackB.replaceClips(with: afterB)
            }
            undoManager?.setActionName("Duplicate")
        }
        undoManager?.setActionName("Duplicate")
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

import Foundation
import AVFoundation
import Combine

class AudioTrack: ObservableObject, Identifiable {
    let id = UUID()
    let label: String

    @Published var clips: [AudioClip] = []
    @Published var volume: Float = 0.8
    @Published var isMuted: Bool = false
    @Published var isSolo: Bool = false
    @Published var eqLow: Float = 0
    @Published var eqMid: Float = 0
    @Published var eqHigh: Float = 0
    @Published var playbackRate: Float = 1.0

    private var clipCancellables: [UUID: AnyCancellable] = [:]

    var name: String {
        if clips.isEmpty { return "Empty" }
        if clips.count == 1 { return clips[0].sourceName }
        return "\(clips[0].sourceName) +\(clips.count - 1)"
    }

    var bpm: Double? {
        clips.first?.bpm
    }

    var isLoaded: Bool { !clips.isEmpty }

    var timelineEnd: TimeInterval {
        clips.map { $0.timelineEnd }.max() ?? 0
    }

    var effectiveDuration: TimeInterval {
        guard let start = clips.map({ $0.timelineStart }).min(),
              let end = clips.map({ $0.timelineEnd }).max() else { return 0 }
        return end - start
    }

    init(label: String) {
        self.label = label
    }

    @MainActor
    func addClip(from fileURL: URL, at clipOffset: TimeInterval = 0) async throws {
        let accessing = fileURL.startAccessingSecurityScopedResource()
        defer { if accessing { fileURL.stopAccessingSecurityScopedResource() } }

        let file = try AVAudioFile(forReading: fileURL)
        let format = file.processingFormat
        let frameCount = UInt32(file.length)
        let duration = Double(frameCount) / format.sampleRate
        let clipName = fileURL.deletingPathExtension().lastPathComponent

        let waveform = try await WaveformGenerator.generate(from: fileURL, targetCount: 4000)
        let detectedBPM = try? await BPMDetector.detect(from: fileURL)

        let clip = AudioClip(
            sourceURL: fileURL, sourceName: clipName,
            sourceDuration: duration, fullWaveform: waveform, bpm: detectedBPM,
            offset: clipOffset
        )

        observeClip(clip)
        clips.append(clip)
    }

    func addClipDirect(_ clip: AudioClip) {
        observeClip(clip)
        clips.append(clip)
    }

    func removeClip(id: UUID) {
        clipCancellables.removeValue(forKey: id)
        clips.removeAll { $0.id == id }
    }

    func replaceClips(with newClips: [AudioClip]) {
        clipCancellables.removeAll()
        clips = newClips
        for clip in clips { observeClip(clip) }
    }

    @discardableResult
    func splitClip(id: UUID, at time: TimeInterval) -> (AudioClip, AudioClip)? {
        guard let idx = clips.firstIndex(where: { $0.id == id }),
              let (left, right) = clips[idx].split(at: time) else { return nil }

        clipCancellables.removeValue(forKey: id)
        clips.remove(at: idx)

        observeClip(left)
        observeClip(right)
        clips.insert(left, at: idx)
        clips.insert(right, at: idx + 1)

        return (left, right)
    }

    func reset() {
        clipCancellables.removeAll()
        clips.removeAll()
        volume = 0.8
        isMuted = false
        isSolo = false
        eqLow = 0
        eqMid = 0
        eqHigh = 0
        playbackRate = 1.0
    }

    private func observeClip(_ clip: AudioClip) {
        clipCancellables[clip.id] = clip.objectWillChange.sink { [weak self] _ in
            self?.objectWillChange.send()
        }
    }
}

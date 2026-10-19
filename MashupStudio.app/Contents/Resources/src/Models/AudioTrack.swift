import Foundation
import AVFoundation
import Combine

class AudioTrack: ObservableObject, Identifiable {
    let id = UUID()
    let label: String

    @Published var url: URL?
    @Published var name: String = "Empty"
    @Published var waveformSamples: [Float] = []
    @Published var duration: TimeInterval = 0
    @Published var offset: TimeInterval = 0
    @Published var trimStart: TimeInterval = 0
    @Published var trimEnd: TimeInterval = 0
    @Published var volume: Float = 0.8
    @Published var isMuted: Bool = false
    @Published var isSolo: Bool = false
    @Published var eqLow: Float = 0
    @Published var eqMid: Float = 0
    @Published var eqHigh: Float = 0
    @Published var playbackRate: Float = 1.0
    @Published var bpm: Double?

    var effectiveDuration: TimeInterval {
        max(0, duration - trimStart - trimEnd)
    }

    var timelineStart: TimeInterval {
        offset + trimStart
    }

    var timelineEnd: TimeInterval {
        offset + duration - trimEnd
    }

    var isLoaded: Bool { url != nil }

    init(label: String) {
        self.label = label
    }

    @MainActor
    func loadFile(_ fileURL: URL) async throws {
        let accessing = fileURL.startAccessingSecurityScopedResource()
        defer { if accessing { fileURL.stopAccessingSecurityScopedResource() } }

        let file = try AVAudioFile(forReading: fileURL)
        let format = file.processingFormat
        let frameCount = UInt32(file.length)

        url = fileURL
        name = fileURL.deletingPathExtension().lastPathComponent
        duration = Double(frameCount) / format.sampleRate
        trimStart = 0
        trimEnd = 0

        waveformSamples = try await WaveformGenerator.generate(
            from: fileURL,
            targetCount: 4000
        )

        bpm = try? await BPMDetector.detect(from: fileURL)
    }

    func reset() {
        url = nil
        name = "Empty"
        waveformSamples = []
        duration = 0
        offset = 0
        trimStart = 0
        trimEnd = 0
        volume = 0.8
        isMuted = false
        isSolo = false
        eqLow = 0
        eqMid = 0
        eqHigh = 0
        playbackRate = 1.0
        bpm = nil
    }
}

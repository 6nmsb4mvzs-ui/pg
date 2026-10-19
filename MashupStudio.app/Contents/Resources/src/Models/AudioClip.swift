import Foundation

class AudioClip: ObservableObject, Identifiable {
    let id: UUID
    let sourceURL: URL
    let sourceName: String
    let sourceDuration: TimeInterval
    let fullWaveform: [Float]
    let bpm: Double?

    @Published var offset: TimeInterval
    @Published var trimStart: TimeInterval
    @Published var trimEnd: TimeInterval

    var effectiveDuration: TimeInterval {
        max(0, sourceDuration - trimStart - trimEnd)
    }

    var timelineStart: TimeInterval {
        offset + trimStart
    }

    var timelineEnd: TimeInterval {
        offset + sourceDuration - trimEnd
    }

    init(sourceURL: URL, sourceName: String, sourceDuration: TimeInterval,
         fullWaveform: [Float], bpm: Double?,
         offset: TimeInterval = 0, trimStart: TimeInterval = 0, trimEnd: TimeInterval = 0) {
        self.id = UUID()
        self.sourceURL = sourceURL
        self.sourceName = sourceName
        self.sourceDuration = sourceDuration
        self.fullWaveform = fullWaveform
        self.bpm = bpm
        self.offset = offset
        self.trimStart = trimStart
        self.trimEnd = trimEnd
    }

    func split(at timelineTime: TimeInterval) -> (AudioClip, AudioClip)? {
        guard timelineTime > timelineStart && timelineTime < timelineEnd else { return nil }
        let localTime = timelineTime - offset

        let left = AudioClip(
            sourceURL: sourceURL, sourceName: sourceName,
            sourceDuration: sourceDuration, fullWaveform: fullWaveform, bpm: bpm,
            offset: offset, trimStart: trimStart, trimEnd: sourceDuration - localTime
        )

        let right = AudioClip(
            sourceURL: sourceURL, sourceName: sourceName,
            sourceDuration: sourceDuration, fullWaveform: fullWaveform, bpm: bpm,
            offset: offset, trimStart: localTime, trimEnd: trimEnd
        )

        return (left, right)
    }
}

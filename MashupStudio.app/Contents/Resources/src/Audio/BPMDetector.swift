import Accelerate
import AVFoundation

struct BPMDetector {
    static func detect(from url: URL) async throws -> Double {
        let accessing = url.startAccessingSecurityScopedResource()
        defer { if accessing { url.stopAccessingSecurityScopedResource() } }

        let file = try AVAudioFile(forReading: url)
        let format = file.processingFormat
        let sampleRate = format.sampleRate
        let frameCount = UInt32(min(file.length, Int64(sampleRate * 30)))

        guard let buffer = AVAudioPCMBuffer(
            pcmFormat: format, frameCapacity: frameCount
        ) else { throw BPMError.bufferFailed }

        try file.read(into: buffer, frameCount: frameCount)

        guard let channelData = buffer.floatChannelData else {
            throw BPMError.noData
        }

        let count = Int(buffer.frameLength)
        let ptr = channelData[0]

        let windowSize = Int(sampleRate * 0.01)
        let hopSize = windowSize / 2
        let windowCount = (count - windowSize) / hopSize
        guard windowCount > 200 else { throw BPMError.tooShort }

        var energy = [Float](repeating: 0, count: windowCount)
        for i in 0..<windowCount {
            let offset = i * hopSize
            vDSP_svesq(ptr + offset, 1, &energy[i], vDSP_Length(windowSize))
        }

        var onset = [Float](repeating: 0, count: windowCount - 1)
        for i in 0..<onset.count {
            onset[i] = max(0, energy[i + 1] - energy[i])
        }

        let onsetRate = sampleRate / Double(hopSize)
        let minLag = Int(onsetRate * 60.0 / 200.0)
        let maxLag = min(Int(onsetRate * 60.0 / 60.0), onset.count - 1)
        guard minLag < maxLag else { throw BPMError.tooShort }

        var bestLag = minLag
        var bestCorr: Float = -.greatestFiniteMagnitude

        onset.withUnsafeBufferPointer { buf in
            for lag in minLag...maxLag {
                var corr: Float = 0
                let n = vDSP_Length(onset.count - lag)
                vDSP_dotpr(buf.baseAddress!, 1,
                           buf.baseAddress! + lag, 1,
                           &corr, n)
                if corr > bestCorr {
                    bestCorr = corr
                    bestLag = lag
                }
            }
        }

        let bpm = onsetRate * 60.0 / Double(bestLag)
        return round(bpm * 10) / 10
    }
}

enum BPMError: LocalizedError {
    case bufferFailed, noData, tooShort
    var errorDescription: String? {
        switch self {
        case .bufferFailed: return "Could not create audio buffer"
        case .noData: return "No audio data"
        case .tooShort: return "Audio too short for BPM detection"
        }
    }
}

import AVFoundation
import Accelerate

enum WaveformGenerator {
    static func generate(from url: URL, targetCount: Int) async throws -> [Float] {
        let file = try AVAudioFile(forReading: url)
        let format = file.processingFormat
        let totalFrames = AVAudioFrameCount(file.length)

        guard totalFrames > 0, let buffer = AVAudioPCMBuffer(
            pcmFormat: format,
            frameCapacity: totalFrames
        ) else {
            return []
        }

        try file.read(into: buffer)

        guard let channelData = buffer.floatChannelData else { return [] }

        let frameCount = Int(buffer.frameLength)
        let channelCount = Int(format.channelCount)

        var mono = [Float](repeating: 0, count: frameCount)
        for ch in 0..<channelCount {
            let ptr = channelData[ch]
            for i in 0..<frameCount {
                mono[i] += ptr[i]
            }
        }
        if channelCount > 1 {
            var divisor = Float(channelCount)
            vDSP_vsdiv(mono, 1, &divisor, &mono, 1, vDSP_Length(frameCount))
        }

        vDSP_vabs(mono, 1, &mono, 1, vDSP_Length(frameCount))

        let samplesPerBucket = max(1, frameCount / targetCount)
        let bucketCount = min(targetCount, frameCount)
        var result = [Float](repeating: 0, count: bucketCount)

        for i in 0..<bucketCount {
            let start = i * samplesPerBucket
            let end = min(start + samplesPerBucket, frameCount)
            let count = end - start
            guard count > 0 else { continue }
            var peak: Float = 0
            vDSP_maxv(Array(mono[start..<end]), 1, &peak, vDSP_Length(count))
            result[i] = peak
        }

        var globalMax: Float = 0
        vDSP_maxv(result, 1, &globalMax, vDSP_Length(bucketCount))
        if globalMax > 0 {
            var scale = 1.0 / globalMax
            vDSP_vsmul(result, 1, &scale, &result, 1, vDSP_Length(bucketCount))
        }

        return result
    }
}

import AVFoundation
import Accelerate

enum AudioExporter {
    static func exportMix(project: MashupProject, to outputURL: URL) async throws {
        let sampleRate: Double = 44100
        let channels: AVAudioChannelCount = 2

        guard let pcmFormat = AVAudioFormat(
            commonFormat: .pcmFormatFloat32,
            sampleRate: sampleRate,
            channels: channels,
            interleaved: false
        ) else {
            throw ExportError.invalidFormat
        }

        let totalDuration = project.totalDuration
        let totalFrames = AVAudioFrameCount(totalDuration * sampleRate)

        guard let outputBuffer = AVAudioPCMBuffer(
            pcmFormat: pcmFormat,
            frameCapacity: totalFrames
        ) else {
            throw ExportError.bufferCreationFailed
        }
        outputBuffer.frameLength = totalFrames

        for ch in 0..<Int(channels) {
            memset(outputBuffer.floatChannelData![ch], 0, Int(totalFrames) * MemoryLayout<Float>.size)
        }

        try mixTrack(project.trackA, into: outputBuffer, sampleRate: sampleRate, channels: channels)
        try mixTrack(project.trackB, into: outputBuffer, sampleRate: sampleRate, channels: channels)

        let masterVol = project.masterVolume
        if masterVol != 1.0 {
            for ch in 0..<Int(channels) {
                var vol = masterVol
                vDSP_vsmul(
                    outputBuffer.floatChannelData![ch], 1,
                    &vol,
                    outputBuffer.floatChannelData![ch], 1,
                    vDSP_Length(totalFrames)
                )
            }
        }

        let outputFile = try AVAudioFile(
            forWriting: outputURL,
            settings: pcmFormat.settings
        )
        try outputFile.write(from: outputBuffer)
    }

    private static func mixTrack(
        _ track: AudioTrack,
        into output: AVAudioPCMBuffer,
        sampleRate: Double,
        channels: AVAudioChannelCount
    ) throws {
        guard !track.isMuted else { return }

        for clip in track.clips {
            try mixClip(clip, volume: track.volume, into: output, sampleRate: sampleRate, channels: channels)
        }
    }

    private static func mixClip(
        _ clip: AudioClip,
        volume: Float,
        into output: AVAudioPCMBuffer,
        sampleRate: Double,
        channels: AVAudioChannelCount
    ) throws {
        let url = clip.sourceURL
        let accessing = url.startAccessingSecurityScopedResource()
        defer { if accessing { url.stopAccessingSecurityScopedResource() } }

        let file = try AVAudioFile(forReading: url)
        let fileFormat = file.processingFormat
        let fileFrameCount = AVAudioFrameCount(file.length)

        guard let fileBuffer = AVAudioPCMBuffer(
            pcmFormat: fileFormat,
            frameCapacity: fileFrameCount
        ) else { return }

        try file.read(into: fileBuffer)

        let trimStartFrame = Int(clip.trimStart * fileFormat.sampleRate)
        let trimEndFrame = Int(clip.trimEnd * fileFormat.sampleRate)
        let usableFrames = Int(fileFrameCount) - trimStartFrame - trimEndFrame
        guard usableFrames > 0 else { return }

        let destStartFrame = Int(clip.timelineStart * sampleRate)
        let framesToCopy = min(usableFrames, Int(output.frameLength) - destStartFrame)
        guard framesToCopy > 0, destStartFrame >= 0 else { return }

        let srcChannels = Int(fileFormat.channelCount)
        let dstChannels = Int(channels)

        for dstCh in 0..<dstChannels {
            let srcCh = min(dstCh, srcChannels - 1)
            let src = fileBuffer.floatChannelData![srcCh].advanced(by: trimStartFrame)
            let dst = output.floatChannelData![dstCh].advanced(by: destStartFrame)

            for i in 0..<framesToCopy {
                dst[i] += src[i] * volume
            }
        }
    }

    enum ExportError: LocalizedError {
        case invalidFormat
        case bufferCreationFailed

        var errorDescription: String? {
            switch self {
            case .invalidFormat: return "Could not create output audio format."
            case .bufferCreationFailed: return "Could not allocate audio buffer."
            }
        }
    }
}

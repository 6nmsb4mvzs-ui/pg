import AVFoundation

enum AudioExporter {
    static func exportMix(project: MashupProject, to outputURL: URL) async throws {
        let sampleRate: Double = 44100
        let channels: AVAudioChannelCount = 2
        let chunkSize: AVAudioFrameCount = 4096

        guard let outputFormat = AVAudioFormat(
            commonFormat: .pcmFormatFloat32,
            sampleRate: sampleRate,
            channels: channels,
            interleaved: false
        ) else {
            throw ExportError.invalidFormat
        }

        let tA = project.trackA
        let tB = project.trackB

        var volA: Float = tA.isMuted ? 0 : tA.volume
        var volB: Float = tB.isMuted ? 0 : tB.volume
        if tA.isSolo || tB.isSolo {
            volA = tA.isSolo ? tA.volume : 0
            volB = tB.isSolo ? tB.volume : 0
        }

        func rateAdjustedEnd(_ track: AudioTrack) -> Double {
            let rate = Double(max(0.1, track.playbackRate))
            return track.clips.map {
                $0.timelineStart + $0.effectiveDuration / rate
            }.max() ?? 0
        }

        let endA = volA > 0 ? rateAdjustedEnd(tA) : 0
        let endB = volB > 0 ? rateAdjustedEnd(tB) : 0
        let totalDuration = max(endA, endB, 0.1)
        let totalFrames = AVAudioFrameCount(totalDuration * sampleRate) + chunkSize

        var accessedURLs: [URL] = []
        let allClips = (volA > 0 ? tA.clips : []) + (volB > 0 ? tB.clips : [])
        for clip in allClips {
            if clip.sourceURL.startAccessingSecurityScopedResource() {
                accessedURLs.append(clip.sourceURL)
            }
        }
        defer { accessedURLs.forEach { $0.stopAccessingSecurityScopedResource() } }

        let engine = AVAudioEngine()

        let mixerA = AVAudioMixerNode()
        let mixerB = AVAudioMixerNode()
        let pitchA = AVAudioUnitTimePitch()
        let pitchB = AVAudioUnitTimePitch()
        let eqA = AVAudioUnitEQ(numberOfBands: 3)
        let eqB = AVAudioUnitEQ(numberOfBands: 3)

        engine.attach(mixerA)
        engine.attach(mixerB)
        engine.attach(pitchA)
        engine.attach(pitchB)
        engine.attach(eqA)
        engine.attach(eqB)

        configureEQBands(eqA)
        configureEQBands(eqB)

        var playersA: [AVAudioPlayerNode] = []
        var playersB: [AVAudioPlayerNode] = []

        for _ in tA.clips {
            let p = AVAudioPlayerNode()
            engine.attach(p)
            engine.connect(p, to: mixerA, format: nil)
            playersA.append(p)
        }
        for _ in tB.clips {
            let p = AVAudioPlayerNode()
            engine.attach(p)
            engine.connect(p, to: mixerB, format: nil)
            playersB.append(p)
        }

        let mainMixer = engine.mainMixerNode
        engine.connect(mixerA, to: pitchA, format: nil)
        engine.connect(pitchA, to: eqA, format: nil)
        engine.connect(eqA, to: mainMixer, format: nil)
        engine.connect(mixerB, to: pitchB, format: nil)
        engine.connect(pitchB, to: eqB, format: nil)
        engine.connect(eqB, to: mainMixer, format: nil)

        mixerA.outputVolume = volA
        mixerB.outputVolume = volB
        mainMixer.outputVolume = project.masterVolume

        pitchA.rate = tA.playbackRate
        pitchB.rate = tB.playbackRate

        eqA.bands[0].gain = tA.eqLow
        eqA.bands[1].gain = tA.eqMid
        eqA.bands[2].gain = tA.eqHigh
        eqB.bands[0].gain = tB.eqLow
        eqB.bands[1].gain = tB.eqMid
        eqB.bands[2].gain = tB.eqHigh

        try engine.enableManualRenderingMode(
            .offline, format: outputFormat, maximumFrameCount: chunkSize
        )
        try engine.start()

        if volA > 0 { scheduleClips(playersA, clips: tA.clips, outputRate: sampleRate) }
        if volB > 0 { scheduleClips(playersB, clips: tB.clips, outputRate: sampleRate) }

        for p in playersA + playersB { p.play() }

        let outputFile = try AVAudioFile(forWriting: outputURL, settings: outputFormat.settings)
        guard let renderBuffer = AVAudioPCMBuffer(
            pcmFormat: outputFormat, frameCapacity: chunkSize
        ) else {
            throw ExportError.bufferCreationFailed
        }

        var framesLeft = totalFrames
        while framesLeft > 0 {
            let toRender = min(chunkSize, framesLeft)
            let status = try engine.renderOffline(toRender, to: renderBuffer)
            switch status {
            case .success, .insufficientDataFromInputNode:
                try outputFile.write(from: renderBuffer)
            case .cannotDoInCurrentContext:
                continue
            case .error:
                throw ExportError.renderFailed
            @unknown default:
                break
            }
            framesLeft -= toRender
        }

        engine.stop()
        engine.disableManualRenderingMode()
    }

    private static func scheduleClips(
        _ players: [AVAudioPlayerNode],
        clips: [AudioClip],
        outputRate: Double
    ) {
        for (i, clip) in clips.enumerated() {
            guard i < players.count else { break }
            guard let file = try? AVAudioFile(forReading: clip.sourceURL) else { continue }

            let sr = file.processingFormat.sampleRate
            let startFrame = AVAudioFramePosition(clip.trimStart * sr)
            let endFrame = AVAudioFramePosition((clip.sourceDuration - clip.trimEnd) * sr)
            let frameCount = AVAudioFrameCount(max(0, endFrame - startFrame))
            guard frameCount > 0 else { continue }

            let clipStart = clip.timelineStart
            if clipStart > 0.001 {
                let delaySamples = AVAudioFramePosition(clipStart * outputRate)
                let at = AVAudioTime(sampleTime: delaySamples, atRate: outputRate)
                players[i].scheduleSegment(
                    file, startingFrame: startFrame, frameCount: frameCount, at: at
                )
            } else {
                players[i].scheduleSegment(
                    file, startingFrame: startFrame, frameCount: frameCount, at: nil
                )
            }
        }
    }

    private static func configureEQBands(_ eq: AVAudioUnitEQ) {
        eq.bands[0].filterType = .lowShelf
        eq.bands[0].frequency = 200
        eq.bands[0].bandwidth = 1.0
        eq.bands[0].bypass = false
        eq.bands[1].filterType = .parametric
        eq.bands[1].frequency = 1000
        eq.bands[1].bandwidth = 1.0
        eq.bands[1].bypass = false
        eq.bands[2].filterType = .highShelf
        eq.bands[2].frequency = 5000
        eq.bands[2].bandwidth = 1.0
        eq.bands[2].bypass = false
    }

    enum ExportError: LocalizedError {
        case invalidFormat
        case bufferCreationFailed
        case renderFailed

        var errorDescription: String? {
            switch self {
            case .invalidFormat: return "Could not create output audio format."
            case .bufferCreationFailed: return "Could not allocate audio buffer."
            case .renderFailed: return "Audio engine rendering failed."
            }
        }
    }
}

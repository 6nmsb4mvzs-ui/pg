import AVFoundation
import Combine

class AudioEngineManager: ObservableObject {
    private let engine = AVAudioEngine()
    private let playerA = AVAudioPlayerNode()
    private let playerB = AVAudioPlayerNode()
    private let pitchA = AVAudioUnitTimePitch()
    private let pitchB = AVAudioUnitTimePitch()
    private let eqA = AVAudioUnitEQ(numberOfBands: 3)
    private let eqB = AVAudioUnitEQ(numberOfBands: 3)

    private var fileA: AVAudioFile?
    private var fileB: AVAudioFile?
    private var displayLink: Timer?
    private var startHostTime: TimeInterval = 0
    private var startProjectTime: TimeInterval = 0

    @Published var isPlaying = false

    weak var project: MashupProject?

    init() {
        configureEQ(eqA)
        configureEQ(eqB)

        engine.attach(playerA)
        engine.attach(playerB)
        engine.attach(pitchA)
        engine.attach(pitchB)
        engine.attach(eqA)
        engine.attach(eqB)

        let mainMixer = engine.mainMixerNode

        engine.connect(playerA, to: pitchA, format: nil)
        engine.connect(pitchA, to: eqA, format: nil)
        engine.connect(eqA, to: mainMixer, format: nil)

        engine.connect(playerB, to: pitchB, format: nil)
        engine.connect(pitchB, to: eqB, format: nil)
        engine.connect(eqB, to: mainMixer, format: nil)
    }

    private func configureEQ(_ eq: AVAudioUnitEQ) {
        let bands = eq.bands
        bands[0].filterType = .lowShelf
        bands[0].frequency = 200
        bands[0].bandwidth = 1.0
        bands[0].bypass = false

        bands[1].filterType = .parametric
        bands[1].frequency = 1000
        bands[1].bandwidth = 1.0
        bands[1].bypass = false

        bands[2].filterType = .highShelf
        bands[2].frequency = 5000
        bands[2].bandwidth = 1.0
        bands[2].bypass = false
    }

    func loadTrackA(url: URL) throws {
        let accessing = url.startAccessingSecurityScopedResource()
        defer { if accessing { url.stopAccessingSecurityScopedResource() } }
        fileA = try AVAudioFile(forReading: url)
    }

    func loadTrackB(url: URL) throws {
        let accessing = url.startAccessingSecurityScopedResource()
        defer { if accessing { url.stopAccessingSecurityScopedResource() } }
        fileB = try AVAudioFile(forReading: url)
    }

    func play(from time: TimeInterval) {
        guard let project else { return }
        stop()

        do {
            if !engine.isRunning {
                try engine.start()
            }
        } catch {
            return
        }

        applySettings()
        scheduleTrack(playerA, file: fileA, track: project.trackA, from: time)
        scheduleTrack(playerB, file: fileB, track: project.trackB, from: time)

        startHostTime = CACurrentMediaTime()
        startProjectTime = time
        isPlaying = true
        project.isPlaying = true

        displayLink = Timer.scheduledTimer(withTimeInterval: 1.0 / 30.0, repeats: true) { [weak self] _ in
            self?.updatePlayhead()
        }
    }

    func stop() {
        playerA.stop()
        playerB.stop()
        displayLink?.invalidate()
        displayLink = nil
        isPlaying = false
        project?.isPlaying = false
    }

    func pause() {
        if isPlaying {
            stop()
        }
    }

    private func scheduleTrack(
        _ player: AVAudioPlayerNode,
        file: AVAudioFile?,
        track: AudioTrack,
        from globalTime: TimeInterval
    ) {
        guard let file, track.isLoaded, !track.isMuted else { return }

        if let project, project.trackA.isSolo || project.trackB.isSolo {
            guard track.isSolo else { return }
        }

        let trackStart = track.timelineStart
        let trackEnd = track.timelineEnd
        guard globalTime < trackEnd else { return }

        let format = file.processingFormat
        let sampleRate = format.sampleRate

        if globalTime >= trackStart {
            let fileOffset = globalTime - track.offset
            let startFrame = AVAudioFramePosition(fileOffset * sampleRate)
            let endFrame = AVAudioFramePosition((track.duration - track.trimEnd) * sampleRate)
            let frameCount = AVAudioFrameCount(max(0, endFrame - startFrame))
            guard frameCount > 0 else { return }

            player.scheduleSegment(
                file,
                startingFrame: startFrame,
                frameCount: frameCount,
                at: nil
            )
            player.play()
        } else {
            let delay = trackStart - globalTime
            let startFrame = AVAudioFramePosition(track.trimStart * sampleRate)
            let endFrame = AVAudioFramePosition((track.duration - track.trimEnd) * sampleRate)
            let frameCount = AVAudioFrameCount(max(0, endFrame - startFrame))
            guard frameCount > 0 else { return }

            let outputSampleRate = engine.mainMixerNode.outputFormat(forBus: 0).sampleRate
            let delaySamples = AVAudioFramePosition(delay * outputSampleRate)
            let startTime = AVAudioTime(
                sampleTime: delaySamples,
                atRate: outputSampleRate
            )

            player.scheduleSegment(
                file,
                startingFrame: startFrame,
                frameCount: frameCount,
                at: startTime
            )
            player.play()
        }
    }

    func applySettings() {
        guard let project else { return }
        let tA = project.trackA
        let tB = project.trackB

        playerA.volume = tA.isMuted ? 0 : tA.volume * project.masterVolume
        playerB.volume = tB.isMuted ? 0 : tB.volume * project.masterVolume

        if tA.isSolo || tB.isSolo {
            playerA.volume = tA.isSolo ? tA.volume * project.masterVolume : 0
            playerB.volume = tB.isSolo ? tB.volume * project.masterVolume : 0
        }

        pitchA.rate = tA.playbackRate
        pitchB.rate = tB.playbackRate

        eqA.bands[0].gain = tA.eqLow
        eqA.bands[1].gain = tA.eqMid
        eqA.bands[2].gain = tA.eqHigh

        eqB.bands[0].gain = tB.eqLow
        eqB.bands[1].gain = tB.eqMid
        eqB.bands[2].gain = tB.eqHigh
    }

    private func updatePlayhead() {
        guard isPlaying, let project else { return }
        let elapsed = CACurrentMediaTime() - startHostTime
        let newTime = startProjectTime + elapsed

        if project.loopEnabled && newTime >= project.loopEnd {
            play(from: project.loopStart)
            return
        }

        if newTime >= project.totalDuration {
            stop()
            project.currentTime = 0
            return
        }

        DispatchQueue.main.async {
            project.currentTime = newTime
        }
    }
}

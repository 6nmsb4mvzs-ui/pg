import AVFoundation
import Combine

class AudioEngineManager: ObservableObject {
    private let engine = AVAudioEngine()

    private let mixerA = AVAudioMixerNode()
    private let mixerB = AVAudioMixerNode()
    private let pitchA = AVAudioUnitTimePitch()
    private let pitchB = AVAudioUnitTimePitch()
    private let eqA = AVAudioUnitEQ(numberOfBands: 3)
    private let eqB = AVAudioUnitEQ(numberOfBands: 3)

    private var poolA: [AVAudioPlayerNode] = []
    private var poolB: [AVAudioPlayerNode] = []
    private let poolSize = 8

    private var fileCache: [URL: AVAudioFile] = [:]
    private var displayLink: Timer?
    private var startHostTime: TimeInterval = 0
    private var startProjectTime: TimeInterval = 0

    @Published var isPlaying = false

    weak var project: MashupProject?

    init() {
        for _ in 0..<poolSize {
            let pA = AVAudioPlayerNode()
            let pB = AVAudioPlayerNode()
            poolA.append(pA)
            poolB.append(pB)
            engine.attach(pA)
            engine.attach(pB)
        }

        engine.attach(mixerA)
        engine.attach(mixerB)
        engine.attach(pitchA)
        engine.attach(pitchB)
        engine.attach(eqA)
        engine.attach(eqB)

        configureEQ(eqA)
        configureEQ(eqB)

        let mainMixer = engine.mainMixerNode

        for p in poolA { engine.connect(p, to: mixerA, format: nil) }
        for p in poolB { engine.connect(p, to: mixerB, format: nil) }

        engine.connect(mixerA, to: pitchA, format: nil)
        engine.connect(pitchA, to: eqA, format: nil)
        engine.connect(eqA, to: mainMixer, format: nil)

        engine.connect(mixerB, to: pitchB, format: nil)
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

    func cacheFile(url: URL) throws {
        guard fileCache[url] == nil else { return }
        let accessing = url.startAccessingSecurityScopedResource()
        defer { if accessing { url.stopAccessingSecurityScopedResource() } }
        fileCache[url] = try AVAudioFile(forReading: url)
    }

    func play(from time: TimeInterval) {
        guard let project else { return }
        stop()

        do {
            if !engine.isRunning { try engine.start() }
        } catch { return }

        applySettings()
        scheduleTrackClips(pool: poolA, track: project.trackA, from: time)
        scheduleTrackClips(pool: poolB, track: project.trackB, from: time)

        startHostTime = CACurrentMediaTime()
        startProjectTime = time
        isPlaying = true
        project.isPlaying = true

        displayLink = Timer.scheduledTimer(withTimeInterval: 1.0 / 30.0, repeats: true) { [weak self] _ in
            self?.updatePlayhead()
        }
    }

    func stop() {
        for p in poolA { p.stop() }
        for p in poolB { p.stop() }
        displayLink?.invalidate()
        displayLink = nil
        isPlaying = false
        project?.isPlaying = false
    }

    func pause() {
        if isPlaying { stop() }
    }

    private func scheduleTrackClips(pool: [AVAudioPlayerNode], track: AudioTrack, from globalTime: TimeInterval) {
        guard !track.isMuted else { return }
        if let project, project.trackA.isSolo || project.trackB.isSolo {
            guard track.isSolo else { return }
        }

        for (i, clip) in track.clips.prefix(poolSize).enumerated() {
            scheduleClip(pool[i], clip: clip, from: globalTime)
        }
    }

    private func scheduleClip(_ player: AVAudioPlayerNode, clip: AudioClip, from globalTime: TimeInterval) {
        guard let file = fileCache[clip.sourceURL] else { return }

        let clipStart = clip.timelineStart
        let clipEnd = clip.timelineEnd
        guard globalTime < clipEnd, clip.effectiveDuration > 0 else { return }

        let sampleRate = file.processingFormat.sampleRate

        if globalTime >= clipStart {
            let sourcePos = globalTime - clip.offset
            let startFrame = AVAudioFramePosition(sourcePos * sampleRate)
            let endFrame = AVAudioFramePosition((clip.sourceDuration - clip.trimEnd) * sampleRate)
            let frameCount = AVAudioFrameCount(max(0, endFrame - startFrame))
            guard frameCount > 0 else { return }

            player.scheduleSegment(file, startingFrame: startFrame, frameCount: frameCount, at: nil)
            player.play()
        } else {
            let delay = clipStart - globalTime
            let startFrame = AVAudioFramePosition(clip.trimStart * sampleRate)
            let endFrame = AVAudioFramePosition((clip.sourceDuration - clip.trimEnd) * sampleRate)
            let frameCount = AVAudioFrameCount(max(0, endFrame - startFrame))
            guard frameCount > 0 else { return }

            let outputRate = engine.mainMixerNode.outputFormat(forBus: 0).sampleRate
            let delaySamples = AVAudioFramePosition(delay * outputRate)
            let startTime = AVAudioTime(sampleTime: delaySamples, atRate: outputRate)

            player.scheduleSegment(file, startingFrame: startFrame, frameCount: frameCount, at: startTime)
            player.play()
        }
    }

    func applySettings() {
        guard let project else { return }
        let tA = project.trackA
        let tB = project.trackB

        var volA = tA.isMuted ? Float(0) : tA.volume * project.masterVolume
        var volB = tB.isMuted ? Float(0) : tB.volume * project.masterVolume

        if tA.isSolo || tB.isSolo {
            volA = tA.isSolo ? tA.volume * project.masterVolume : 0
            volB = tB.isSolo ? tB.volume * project.masterVolume : 0
        }

        mixerA.outputVolume = volA
        mixerB.outputVolume = volB

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

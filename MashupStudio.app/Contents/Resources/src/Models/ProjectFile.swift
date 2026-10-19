import Foundation
import AVFoundation

struct ProjectFileData: Codable {
    var version: String = "1.0"
    var trackA: TrackFileData
    var trackB: TrackFileData
    var masterVolume: Float
    var zoomLevel: Double
    var loopEnabled: Bool
    var loopStart: Double
    var loopEnd: Double
    var crossfadeDuration: Double
}

struct TrackFileData: Codable {
    var clips: [ClipFileData]
    var volume: Float
    var isMuted: Bool
    var isSolo: Bool
    var eqLow: Float
    var eqMid: Float
    var eqHigh: Float
    var playbackRate: Float
}

struct ClipFileData: Codable {
    var sourcePath: String
    var sourceName: String
    var sourceDuration: Double
    var offset: Double
    var trimStart: Double
    var trimEnd: Double
}

enum ProjectFile {
    static func save(project: MashupProject, to url: URL) throws {
        let data = ProjectFileData(
            trackA: trackData(from: project.trackA),
            trackB: trackData(from: project.trackB),
            masterVolume: project.masterVolume,
            zoomLevel: project.zoomLevel,
            loopEnabled: project.loopEnabled,
            loopStart: project.loopStart,
            loopEnd: project.loopEnd,
            crossfadeDuration: project.crossfadeDuration
        )
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        let json = try encoder.encode(data)
        try json.write(to: url)
    }

    @MainActor
    static func load(from url: URL, into project: MashupProject, engine: AudioEngineManager) async throws {
        let json = try Data(contentsOf: url)
        let data = try JSONDecoder().decode(ProjectFileData.self, from: json)

        engine.stop()
        project.trackA.reset()
        project.trackB.reset()

        try await loadTrack(data.trackA, into: project.trackA, engine: engine)
        try await loadTrack(data.trackB, into: project.trackB, engine: engine)

        project.masterVolume = data.masterVolume
        project.zoomLevel = data.zoomLevel
        project.loopEnabled = data.loopEnabled
        project.loopStart = data.loopStart
        project.loopEnd = data.loopEnd
        project.crossfadeDuration = data.crossfadeDuration
        project.currentTime = 0
    }

    private static func trackData(from track: AudioTrack) -> TrackFileData {
        TrackFileData(
            clips: track.clips.map { clip in
                ClipFileData(
                    sourcePath: clip.sourceURL.path,
                    sourceName: clip.sourceName,
                    sourceDuration: clip.sourceDuration,
                    offset: clip.offset,
                    trimStart: clip.trimStart,
                    trimEnd: clip.trimEnd
                )
            },
            volume: track.volume,
            isMuted: track.isMuted,
            isSolo: track.isSolo,
            eqLow: track.eqLow,
            eqMid: track.eqMid,
            eqHigh: track.eqHigh,
            playbackRate: track.playbackRate
        )
    }

    @MainActor
    private static func loadTrack(
        _ data: TrackFileData,
        into track: AudioTrack,
        engine: AudioEngineManager
    ) async throws {
        for clipData in data.clips {
            let url = URL(fileURLWithPath: clipData.sourcePath)
            guard FileManager.default.fileExists(atPath: clipData.sourcePath) else { continue }

            try? engine.cacheFile(url: url)

            let waveform = try await WaveformGenerator.generate(from: url, targetCount: 4000)
            let bpm = try? await BPMDetector.detect(from: url)

            let clip = AudioClip(
                sourceURL: url,
                sourceName: clipData.sourceName,
                sourceDuration: clipData.sourceDuration,
                fullWaveform: waveform,
                bpm: bpm,
                offset: clipData.offset,
                trimStart: clipData.trimStart,
                trimEnd: clipData.trimEnd
            )
            track.addClipDirect(clip)
        }

        track.volume = data.volume
        track.isMuted = data.isMuted
        track.isSolo = data.isSolo
        track.eqLow = data.eqLow
        track.eqMid = data.eqMid
        track.eqHigh = data.eqHigh
        track.playbackRate = data.playbackRate
    }

    enum ProjectFileError: LocalizedError {
        case missingSourceFiles([String])

        var errorDescription: String? {
            switch self {
            case .missingSourceFiles(let names):
                return "Missing audio files: \(names.joined(separator: ", "))"
            }
        }
    }
}

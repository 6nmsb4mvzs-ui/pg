import Foundation

enum MusicImporter {
    @MainActor
    static func importFile(_ fileURL: URL) async -> Bool {
        let path = fileURL.path
            .replacingOccurrences(of: "\\", with: "\\\\")
            .replacingOccurrences(of: "\"", with: "\\\"")
        let source = """
        tell application "Music"
            add POSIX file "\(path)"
        end tell
        """
        guard let script = NSAppleScript(source: source) else { return false }
        var error: NSDictionary?
        script.executeAndReturnError(&error)
        return error == nil
    }
}

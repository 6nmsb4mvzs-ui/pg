import SwiftUI

struct ClipView: View {
    @ObservedObject var clip: AudioClip
    @ObservedObject var project: MashupProject
    @Environment(\.undoManager) var undoManager

    let color: Color
    let height: CGFloat

    @State private var isDragging = false
    @State private var dragStartOffset: TimeInterval = 0
    @State private var isDraggingTrimStart = false
    @State private var isDraggingTrimEnd = false
    @State private var trimDragOrigin: TimeInterval = 0
    @State private var isHovering = false
    @State private var isDraggingFadeIn = false
    @State private var isDraggingFadeOut = false
    @State private var fadeDragOrigin: TimeInterval = 0

    private let trimHandleWidth: CGFloat = 8
    private let fadeHandleSize: CGFloat = 10

    var body: some View {
        let pps = project.pixelsPerSecond
        let clipX = project.timeToX(clip.offset)
        let sourceWidth = CGFloat(clip.sourceDuration * pps)
        let trimStartPx = CGFloat(clip.trimStart * pps)
        let trimEndPx = CGFloat(clip.trimEnd * pps)
        let visibleWidth = max(1, sourceWidth - trimStartPx - trimEndPx)

        ZStack(alignment: .leading) {
            if trimStartPx > 1 {
                Rectangle()
                    .fill(color.opacity(0.05))
                    .frame(width: trimStartPx, height: height)
                    .offset(x: clipX)
                    .allowsHitTesting(false)
            }
            if trimEndPx > 1 {
                Rectangle()
                    .fill(color.opacity(0.05))
                    .frame(width: trimEndPx, height: height)
                    .offset(x: clipX + sourceWidth - trimEndPx)
                    .allowsHitTesting(false)
            }

            waveformShape(width: visibleWidth, height: height)
                .fill(
                    LinearGradient(
                        colors: [color.opacity(0.85), color.opacity(0.4)],
                        startPoint: .top,
                        endPoint: .bottom
                    )
                )
                .frame(width: visibleWidth, height: height)
                .background(
                    RoundedRectangle(cornerRadius: 5)
                        .fill(color.opacity(isHovering ? 0.16 : 0.08))
                )
                .overlay(fadeOverlay(width: visibleWidth, height: height))
                .clipShape(RoundedRectangle(cornerRadius: 5))
                .overlay(
                    RoundedRectangle(cornerRadius: 5)
                        .stroke(
                            isDragging ? Color.white.opacity(0.7) :
                            isHovering ? color.opacity(0.6) : color.opacity(0.3),
                            lineWidth: isDragging ? 1.5 : 1
                        )
                )
                .shadow(color: color.opacity(isDragging ? 0.25 : 0), radius: 8)
                .offset(x: clipX + trimStartPx)
                .gesture(dragGesture())
                .onHover { isHovering = $0 }

            trimHandle(isStart: true)
                .offset(x: clipX + trimStartPx - trimHandleWidth / 2)
                .gesture(trimStartGesture())

            trimHandle(isStart: false)
                .offset(x: clipX + trimStartPx + visibleWidth - trimHandleWidth / 2)
                .gesture(trimEndGesture())

            // Fade-in handle
            if isHovering || clip.fadeIn > 0 || isDraggingFadeIn {
                let fadeInPx = CGFloat(clip.fadeIn * pps)
                fadeHandle()
                    .offset(
                        x: clipX + trimStartPx + fadeInPx - fadeHandleSize / 2,
                        y: -height / 2 + fadeHandleSize / 2 + 2
                    )
                    .gesture(fadeInGesture())
            }

            // Fade-out handle
            if isHovering || clip.fadeOut > 0 || isDraggingFadeOut {
                let fadeOutPx = CGFloat(clip.fadeOut * pps)
                fadeHandle()
                    .offset(
                        x: clipX + trimStartPx + visibleWidth - fadeOutPx - fadeHandleSize / 2,
                        y: -height / 2 + fadeHandleSize / 2 + 2
                    )
                    .gesture(fadeOutGesture())
            }

            Text(clip.sourceName)
                .font(.system(size: 10, weight: .medium, design: .monospaced))
                .foregroundColor(.white.opacity(0.65))
                .lineLimit(1)
                .padding(.horizontal, 5)
                .padding(.vertical, 2)
                .background(Color.black.opacity(0.45))
                .cornerRadius(3)
                .offset(x: clipX + trimStartPx + 4, y: -height / 2 + 12)
                .allowsHitTesting(false)

            if let bpm = clip.bpm, visibleWidth > 80 {
                Text("\(String(format: "%.0f", bpm))")
                    .font(.system(size: 8, weight: .medium, design: .monospaced))
                    .foregroundColor(color.opacity(0.5))
                    .offset(x: clipX + trimStartPx + 4, y: height / 2 - 14)
                    .allowsHitTesting(false)
            }
        }
        .frame(height: height)
    }

    private func fadeOverlay(width: CGFloat, height: CGFloat) -> some View {
        let pps = project.pixelsPerSecond
        let fadeInPx = CGFloat(clip.fadeIn * pps)
        let fadeOutPx = CGFloat(clip.fadeOut * pps)

        return ZStack(alignment: .leading) {
            if fadeInPx > 1 {
                Path { p in
                    p.move(to: CGPoint(x: 0, y: 0))
                    p.addLine(to: CGPoint(x: fadeInPx, y: 0))
                    p.addLine(to: CGPoint(x: 0, y: height))
                    p.closeSubpath()
                }
                .fill(Color.black.opacity(0.4))

                Path { p in
                    p.move(to: CGPoint(x: 0, y: height))
                    p.addLine(to: CGPoint(x: fadeInPx, y: 0))
                }
                .stroke(Color.white.opacity(0.5), lineWidth: 1)
            }

            if fadeOutPx > 1 {
                Path { p in
                    p.move(to: CGPoint(x: width, y: 0))
                    p.addLine(to: CGPoint(x: width - fadeOutPx, y: 0))
                    p.addLine(to: CGPoint(x: width, y: height))
                    p.closeSubpath()
                }
                .fill(Color.black.opacity(0.4))

                Path { p in
                    p.move(to: CGPoint(x: width - fadeOutPx, y: 0))
                    p.addLine(to: CGPoint(x: width, y: height))
                }
                .stroke(Color.white.opacity(0.5), lineWidth: 1)
            }
        }
        .frame(width: width, height: height)
    }

    private func fadeHandle() -> some View {
        Circle()
            .fill(Color.white.opacity(0.8))
            .frame(width: fadeHandleSize, height: fadeHandleSize)
            .overlay(
                Circle().stroke(color, lineWidth: 1)
            )
            .shadow(color: .black.opacity(0.4), radius: 2)
            .contentShape(Rectangle().size(width: fadeHandleSize + 12, height: fadeHandleSize + 12))
            .onHover { hovering in
                if hovering { NSCursor.resizeLeftRight.push() }
                else { NSCursor.pop() }
            }
    }

    private func waveformShape(width: CGFloat, height: CGFloat) -> Path {
        let samples = clip.fullWaveform
        guard !samples.isEmpty, clip.sourceDuration > 0 else { return Path() }

        let totalSamples = samples.count
        let trimStartFrac = clip.trimStart / clip.sourceDuration
        let trimEndFrac = clip.trimEnd / clip.sourceDuration
        let startIdx = max(0, Int(Double(totalSamples) * trimStartFrac))
        let endIdx = min(totalSamples, totalSamples - Int(Double(totalSamples) * trimEndFrac))
        guard startIdx < endIdx else { return Path() }
        let visible = Array(samples[startIdx..<endIdx])
        guard !visible.isEmpty else { return Path() }

        return Path { path in
            let count = visible.count
            let step = width / CGFloat(count)
            let mid = height / 2

            path.move(to: CGPoint(x: 0, y: mid))
            for i in 0..<count {
                let x = CGFloat(i) * step
                let amp = CGFloat(visible[i]) * mid * 0.85
                path.addLine(to: CGPoint(x: x, y: mid - amp))
            }
            path.addLine(to: CGPoint(x: width, y: mid))
            for i in stride(from: count - 1, through: 0, by: -1) {
                let x = CGFloat(i) * step
                let amp = CGFloat(visible[i]) * mid * 0.85
                path.addLine(to: CGPoint(x: x, y: mid + amp))
            }
            path.closeSubpath()
        }
    }

    private func trimHandle(isStart: Bool) -> some View {
        ZStack {
            RoundedRectangle(cornerRadius: 2)
                .fill(Color.yellow.opacity(0.8))
                .frame(width: trimHandleWidth, height: height * 0.6)
                .overlay(
                    RoundedRectangle(cornerRadius: 2)
                        .stroke(Color.yellow, lineWidth: 0.5)
                )
                .shadow(color: .black.opacity(0.3), radius: 2)
        }
        .frame(width: trimHandleWidth + 8, height: height)
        .contentShape(Rectangle())
        .onHover { hovering in
            if hovering { NSCursor.resizeLeftRight.push() }
            else { NSCursor.pop() }
        }
    }

    private func dragGesture() -> some Gesture {
        DragGesture(minimumDistance: 3)
            .onChanged { value in
                if !isDragging {
                    isDragging = true
                    dragStartOffset = clip.offset
                    NSCursor.closedHand.push()
                }
                let delta = Double(value.translation.width) / project.pixelsPerSecond
                clip.offset = max(-clip.trimStart, dragStartOffset + delta)
            }
            .onEnded { _ in
                NSCursor.pop()
                isDragging = false
                if clip.offset != dragStartOffset {
                    let finalOff = clip.offset
                    clip.offset = dragStartOffset
                    project.setClipOffset(clip, to: finalOff, undoManager: undoManager)
                }
            }
    }

    private func trimStartGesture() -> some Gesture {
        DragGesture(minimumDistance: 1)
            .onChanged { value in
                if !isDraggingTrimStart {
                    isDraggingTrimStart = true
                    trimDragOrigin = clip.trimStart
                }
                let delta = Double(value.translation.width) / project.pixelsPerSecond
                clip.trimStart = max(0, min(trimDragOrigin + delta, clip.sourceDuration - clip.trimEnd - 0.1))
            }
            .onEnded { _ in
                isDraggingTrimStart = false
                let finalTrim = clip.trimStart
                clip.trimStart = trimDragOrigin
                project.setClipTrimStart(clip, to: finalTrim, undoManager: undoManager)
            }
    }

    private func trimEndGesture() -> some Gesture {
        DragGesture(minimumDistance: 1)
            .onChanged { value in
                if !isDraggingTrimEnd {
                    isDraggingTrimEnd = true
                    trimDragOrigin = clip.trimEnd
                }
                let delta = -Double(value.translation.width) / project.pixelsPerSecond
                clip.trimEnd = max(0, min(trimDragOrigin + delta, clip.sourceDuration - clip.trimStart - 0.1))
            }
            .onEnded { _ in
                isDraggingTrimEnd = false
                let finalTrim = clip.trimEnd
                clip.trimEnd = trimDragOrigin
                project.setClipTrimEnd(clip, to: finalTrim, undoManager: undoManager)
            }
    }

    private func fadeInGesture() -> some Gesture {
        DragGesture(minimumDistance: 1)
            .onChanged { value in
                if !isDraggingFadeIn {
                    isDraggingFadeIn = true
                    fadeDragOrigin = clip.fadeIn
                }
                let delta = Double(value.translation.width) / project.pixelsPerSecond
                let maxFade = clip.effectiveDuration - clip.fadeOut
                clip.fadeIn = max(0, min(fadeDragOrigin + delta, maxFade))
            }
            .onEnded { _ in
                isDraggingFadeIn = false
                let finalFade = clip.fadeIn
                clip.fadeIn = fadeDragOrigin
                project.setClipFadeIn(clip, to: finalFade, undoManager: undoManager)
            }
    }

    private func fadeOutGesture() -> some Gesture {
        DragGesture(minimumDistance: 1)
            .onChanged { value in
                if !isDraggingFadeOut {
                    isDraggingFadeOut = true
                    fadeDragOrigin = clip.fadeOut
                }
                let delta = -Double(value.translation.width) / project.pixelsPerSecond
                let maxFade = clip.effectiveDuration - clip.fadeIn
                clip.fadeOut = max(0, min(fadeDragOrigin + delta, maxFade))
            }
            .onEnded { _ in
                isDraggingFadeOut = false
                let finalFade = clip.fadeOut
                clip.fadeOut = fadeDragOrigin
                project.setClipFadeOut(clip, to: finalFade, undoManager: undoManager)
            }
    }
}

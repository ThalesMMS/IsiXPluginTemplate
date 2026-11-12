//  Copyright (c) 2026 Thales Matheus M Santos (ThalesMMS)
//
//  Distributed under the MIT License: see LICENSE at the root of this project.
//  SPDX-License-Identifier: MIT

import Foundation
import IsiXPluginKit

/// The minimum, maximum and mean of an image's pixels.
struct PixelStatistics: Equatable, Sendable {
    var count: Int
    var mean: Double
    var minimum: Double
    var maximum: Double
}

/// What the commands compute, apart from the user interface: plain functions
/// of the values the kit returns, safe to call on any thread.
enum ImageReport {
    /// The statistics of the samples: modality values for a grayscale image,
    /// the mean of red, green and blue for a colour one. Returns nil when
    /// `isCancelled` becomes true, which it is asked every 64 Ki pixels.
    static func statistics(of pixels: IsiXPixelData, isCancelled: () -> Bool) -> PixelStatistics? {
        var count = 0, sum = 0.0, minimum = Double.infinity, maximum = -Double.infinity
        func add(_ value: Double) {
            count += 1
            sum += value
            minimum = min(minimum, value)
            maximum = max(maximum, value)
        }
        switch pixels {
        case .float32(let buffer):
            let finished = buffer.withUnsafeBufferPointer { samples -> Bool in
                for (index, sample) in samples.enumerated() {
                    if index % 65_536 == 0, isCancelled() { return false }
                    add(Double(sample))
                }
                return true
            }
            if !finished { return nil }
        case .rgba8(let buffer):
            let finished = buffer.withUnsafeBufferPointer { samples -> Bool in
                var index = 0
                while index + 3 < samples.count {
                    if (index / 4) % 65_536 == 0, isCancelled() { return false }
                    add((Double(samples[index]) + Double(samples[index + 1]) + Double(samples[index + 2])) / 3)
                    index += 4
                }
                return true
            }
            if !finished { return nil }
        @unknown default:
            return nil
        }
        guard count > 0 else { return PixelStatistics(count: 0, mean: 0, minimum: 0, maximum: 0) }
        return PixelStatistics(count: count, mean: sum / Double(count), minimum: minimum, maximum: maximum)
    }

    /// The image's size, spacing, position and orientation, and where its
    /// centre is in patient coordinates, in one line.
    static func geometrySummary(_ geometry: IsiXImageGeometry) -> String {
        let centre = geometry.patientPoint(of: centrePixel(of: geometry))
        return String(format: "%d×%d px, spacing %.4f/%.4f mm, position %.3f, %.3f, %.3f mm, orientation %@, centre %.3f, %.3f, %.3f mm",
                      geometry.columns, geometry.rows, geometry.rowSpacing, geometry.columnSpacing,
                      geometry.imagePosition.x, geometry.imagePosition.y, geometry.imagePosition.z,
                      geometry.imageOrientation.map { String(format: "%.4f", $0) }.joined(separator: ", "),
                      centre.x, centre.y, centre.z)
    }

    /// The centre of the image in pixel coordinates, where the centre of the
    /// first pixel is (0, 0).
    static func centrePixel(of geometry: IsiXImageGeometry) -> IsiXPixelPoint {
        IsiXPixelPoint(x: Double(geometry.columns - 1) / 2, y: Double(geometry.rows - 1) / 2)
    }

    /// An oval or a rectangle around the image's centre, its size a
    /// percentage of the image's, on the given slice and phase.
    static func centreROI(in geometry: IsiXImageGeometry, slice: Int, phase: Int, settings: PluginSettings) -> IsiXROIDraft {
        let centre = centrePixel(of: geometry)
        let fraction = Double(settings.roiSizePercent) / 100
        let halfWidth = Double(geometry.columns) * fraction / 2
        let halfHeight = Double(geometry.rows) * fraction / 2
        let corners = [IsiXPixelPoint(x: centre.x - halfWidth, y: centre.y - halfHeight),
                       IsiXPixelPoint(x: centre.x + halfWidth, y: centre.y + halfHeight)]
        let kind: IsiXROIKind = settings.roiShape == .oval ? .oval : .rectangle
        return IsiXROIDraft(kind: kind, points: corners, name: settings.roiName, slice: slice, phase: phase)
    }
}

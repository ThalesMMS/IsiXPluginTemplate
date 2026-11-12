//  Copyright (c) 2026 Thales Matheus M Santos (ThalesMMS)
//
//  Distributed under the MIT License: see LICENSE at the root of this project.
//  SPDX-License-Identifier: MIT

import Foundation

/// What the user can change in the settings window.
///
/// The starting values come from `Defaults.plist` in the plugin's resources.
/// Changes last until the application quits: the example does not write them
/// anywhere. A plugin that keeps settings should store them in a file of its
/// own, not in the application's preferences.
struct PluginSettings: Equatable, Sendable {
    enum ROIShape: String, CaseIterable, Sendable {
        case oval
        case rectangle
    }

    var roiName: String
    var roiShape: ROIShape
    /// The ROI's width and height, as a percentage of the image's.
    var roiSizePercent: Int
    /// Whether a result is also shown in a sheet on the viewer's window.
    var showResultSheet: Bool

    static let sizeRange = 10...90

    /// The values of `Defaults.plist` in `bundle`.
    init(defaultsIn bundle: Bundle) throws {
        guard let url = bundle.url(forResource: "Defaults", withExtension: "plist") else {
            throw PluginFailure.missingResource("Defaults.plist")
        }
        let data = try Data(contentsOf: url)
        guard let values = try PropertyListSerialization.propertyList(from: data, format: nil) as? [String: Any],
              let name = values["ROIName"] as? String,
              let shapeName = values["ROIKind"] as? String, let shape = ROIShape(rawValue: shapeName),
              let size = values["ROISizePercent"] as? Int,
              let sheet = values["ShowResultSheet"] as? Bool else {
            throw PluginFailure.invalidResource("Defaults.plist")
        }
        self.init(roiName: name, roiShape: shape, roiSizePercent: size, showResultSheet: sheet)
    }

    init(roiName: String, roiShape: ROIShape, roiSizePercent: Int, showResultSheet: Bool) {
        self.roiName = roiName
        self.roiShape = roiShape
        self.roiSizePercent = min(max(roiSizePercent, Self.sizeRange.lowerBound), Self.sizeRange.upperBound)
        self.showResultSheet = showResultSheet
    }
}

/// Why a command of the plugin did not do its work. The application shows the
/// description to the user.
enum PluginFailure: Error, CustomStringConvertible {
    case noViewer(String)
    case missingResource(String)
    case invalidResource(String)

    var description: String {
        switch self {
        case .noViewer(let message): return message
        case .missingResource(let name): return "The plugin's resource \(name) is missing. Reinstall the plugin."
        case .invalidResource(let name): return "The plugin's resource \(name) cannot be read. Reinstall the plugin."
        }
    }
}

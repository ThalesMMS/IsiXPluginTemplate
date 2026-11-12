//  Copyright (c) 2026 Thales Matheus M Santos (ThalesMMS)
//
//  Distributed under the MIT License: see LICENSE at the root of this project.
//  SPDX-License-Identifier: MIT

import AppKit
import IsiXPluginKit
import os
import Synchronization

/// The plugin's entry point: the class that adopts `IsiXPlugin`.
///
/// Info.plist names it under `NSPrincipalClass` as
/// `$(PRODUCT_MODULE_NAME).$(ISIX_PLUGIN_ENTRY_CLASS)`, which the build expands
/// to `MyIsiXPlugin.MyIsiXPlugin`: the Swift module, then the class. If you
/// rename the class, change `ISIX_PLUGIN_ENTRY_CLASS` in
/// Config/MyIsiXPlugin.xcconfig to match.
///
/// The application creates one instance with `init()`, calls
/// `activate(with:)` once after the bundle passed its checks, and
/// `deactivate()` once before it quits, all on the main actor.
///
/// Three commands show the API: the settings window, reading the frontmost
/// viewer's image with its geometry and pixels, and creating a ROI at its
/// centre. Without a viewer, the two viewer commands are disabled in the menus
/// and, if they run anyway, end with a message instead of touching anything.
public final class MyIsiXPlugin: IsiXPlugin, CustomStringConvertible {
    /// The command identifiers: the application tells commands apart by them, never by their titles.
    enum Command {
        static let settings = "settings"
        static let inspectImage = "inspect-image"
        static let markCentre = "mark-centre"
    }

    private var context: IsiXPluginContext?
    private var log = Logger(subsystem: "MyIsiXPlugin", category: "plugin")
    private var settings = PluginSettings(roiName: "ROI", roiShape: .oval, roiSizePercent: 50, showResultSheet: false)
    private var settingsWindow: SettingsWindowController?
    private var observation: IsiXObservation?
    /// A measurement running for a viewer, cancelled if that viewer closes.
    private struct Measurement {
        let token: UUID
        let task: IsiXTask
        /// Where to show the result: the viewer's window, not kept open by the plugin.
        weak var window: NSWindow?
    }

    private var measurements: [IsiXViewerID: Measurement] = [:]
    private var lastResult: String?

    /// What the plugin did, one line per step, kept for whoever debugs it.
    /// `description` returns it, so `po` on the plugin in the debugger shows it.
    private let journal = Mutex<[String]>([])

    public nonisolated var description: String { journal.withLock { $0.joined(separator: "\n") } }

    public init() {}

    // MARK: lifecycle

    public func activate(with context: IsiXPluginContext) throws {
        self.context = context
        log = Logger(subsystem: context.info.identifier, category: "plugin")
        // Resources come from the plugin's own bundle, never from the application's.
        settings = try PluginSettings(defaultsIn: context.bundle)

        try context.register(IsiXCommand(id: Command.settings, title: text("command.settings"),
                                         placement: .other, requirement: .none)) { [weak self] _ in
            self?.showSettings()
        }
        try context.register(IsiXCommand(id: Command.inspectImage, title: text("command.inspectImage"),
                                         placement: .imageFilters, requirement: .viewer)) { [weak self] invocation in
            try self?.inspectImage(invocation)
        }
        try context.register(IsiXCommand(id: Command.markCentre, title: text("command.markCentre"),
                                         placement: .roiTools, requirement: .viewer)) { [weak self] invocation in
            try self?.markCentre(invocation)
        }
        observation = try context.observe([.viewerWillClose]) { [weak self] event in
            self?.handle(event)
        }
        note("activated \(context.info.identifier) \(context.info.version) from \(context.bundle.bundleURL.lastPathComponent); "
             + "defaults: roi \(settings.roiShape.rawValue) \(settings.roiSizePercent)% named \(settings.roiName)")
    }

    public func deactivate() {
        for measurement in measurements.values { measurement.task.cancel() }
        measurements.removeAll()
        observation = nil
        settingsWindow?.close()
        settingsWindow = nil
        context = nil
        note("deactivated")
    }

    // MARK: commands

    /// Opens the settings window, or brings it forward if it is open.
    private func showSettings() {
        if settingsWindow == nil {
            settingsWindow = SettingsWindowController(settings: settings, lastResult: lastResult, strings: { [weak self] in self?.text($0) ?? $0 }) { [weak self] edited in
                guard let self else { return }
                self.settings = edited
                self.settingsWindow = nil
                self.note("settings closed: roi \(edited.roiShape.rawValue) \(edited.roiSizePercent)% named \(edited.roiName), sheet \(edited.showResultSheet)")
            }
            note("settings opened")
        }
        settingsWindow?.showWindow(nil)
    }

    /// Reads the current image and its geometry on the main actor, then
    /// measures the copy in a task. The copy stays valid if the viewer closes;
    /// the task is cancelled if it does.
    private func inspectImage(_ invocation: IsiXCommandInvocation) throws {
        let viewer = try openViewer(of: invocation)
        let image = try viewer.image()
        let series = try viewer.series()
        let geometry = ImageReport.geometrySummary(image.geometry)
        note("image slice \(image.slice + 1) of \(series.sliceCount), phase \(image.phase): \(geometry)")

        let viewerID = viewer.id
        measurements.removeValue(forKey: viewerID)?.task.cancel()
        guard let context else { return }
        // The operation gets only Sendable copies: the image, the viewer's identifier and a token.
        let token = UUID()
        let task = try context.startTask(named: "Measure image") { progress in
            let statistics = ImageReport.statistics(of: image.pixels) { progress.isCancelled || Task.isCancelled }
            await MainActor.run { [weak self] in
                self?.finishInspection(statistics, slice: image.slice, viewerID: viewerID, token: token)
            }
        }
        measurements[viewerID] = Measurement(token: token, task: task, window: invocation.parentWindow)
    }

    /// Shows the result of a measurement, unless it was cancelled: its viewer
    /// closed, or a newer measurement of the same viewer replaced it.
    private func finishInspection(_ statistics: PixelStatistics?, slice: Int, viewerID: IsiXViewerID, token: UUID) {
        guard let measurement = measurements[viewerID], measurement.token == token,
              !measurement.task.progress.isCancelled, let statistics else {
            note(text("error.cancelled"))
            return
        }
        measurements.removeValue(forKey: viewerID)
        let result = String(format: "Slice %d: %d pixels, mean %.4f, minimum %.4f, maximum %.4f",
                            slice + 1, statistics.count, statistics.mean, statistics.minimum, statistics.maximum)
        report(result, in: measurement.window)
    }

    /// Adds an oval or a rectangle around the centre of the current image and
    /// asks the application for its statistics.
    private func markCentre(_ invocation: IsiXCommandInvocation) throws {
        let viewer = try openViewer(of: invocation)
        let image = try viewer.image()
        let draft = ImageReport.centreROI(in: image.geometry, slice: image.slice, phase: image.phase, settings: settings)
        let roi = try viewer.addROI(draft)
        let statistics = try viewer.statistics(of: roi.id)
        let corners = roi.patientPoints.map { String(format: "(%.3f, %.3f, %.3f)", $0.x, $0.y, $0.z) }.joined(separator: " ")
        let result = String(format: "ROI \"%@\" (%@) on slice %d: mean %.4f, minimum %.4f, maximum %.4f, area %.4f mm²; corners %@",
                            roi.name, settings.roiShape.rawValue, roi.slice + 1, statistics.mean, statistics.minimum,
                            statistics.maximum, statistics.area ?? 0, corners)
        report(result, in: invocation.parentWindow)
    }

    /// The invocation's viewer, or an error the application shows to the user.
    private func openViewer(of invocation: IsiXCommandInvocation) throws -> IsiXViewer {
        guard let viewer = invocation.viewer, viewer.isOpen else {
            note("no viewer: \(invocation.command.id) did nothing")
            throw PluginFailure.noViewer(text("error.noViewer"))
        }
        return viewer
    }

    /// Records a result, shows it in the settings window and, if the settings
    /// say so, in a sheet on the viewer's window. The sheet does not block the
    /// application.
    private func report(_ result: String, in parentWindow: NSWindow?) {
        lastResult = result
        note(result)
        settingsWindow?.show(result: result)
        guard settings.showResultSheet, let parentWindow, parentWindow.isVisible, parentWindow.attachedSheet == nil else { return }
        let alert = NSAlert()
        alert.messageText = text("result.title")
        alert.informativeText = result
        alert.beginSheetModal(for: parentWindow)
    }

    // MARK: events

    private func handle(_ event: IsiXEvent) {
        switch event {
        case .viewerWillClose(let id):
            if let measurement = measurements.removeValue(forKey: id) {
                measurement.task.cancel()
                note("viewer closing: measurement cancelled")
            } else {
                note("viewer closing")
            }
        default:
            break
        }
    }

    // MARK: support

    private func text(_ key: String) -> String {
        (context?.bundle ?? Bundle(for: MyIsiXPlugin.self)).localizedString(forKey: key, value: key, table: nil)
    }

    private func note(_ line: String) {
        journal.withLock { $0.append(line) }
        log.notice("\(line, privacy: .public)")
    }
}

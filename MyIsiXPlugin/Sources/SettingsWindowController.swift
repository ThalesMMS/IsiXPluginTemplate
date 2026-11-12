//  Copyright (c) 2026 Thales Matheus M Santos (ThalesMMS)
//
//  Distributed under the MIT License: see LICENSE at the root of this project.
//  SPDX-License-Identifier: MIT

import AppKit

/// The plugin's settings window: a regular window, not modal, so the user can
/// keep working in the viewers while it is open. Closing it, with Done or with
/// its close button, hands the edited settings back through `onClose`.
@MainActor
final class SettingsWindowController: NSWindowController, NSWindowDelegate {
    private let strings: (String) -> String
    private let onClose: (PluginSettings) -> Void
    private var settings: PluginSettings

    private let nameField = NSTextField()
    private let shapePopUp = NSPopUpButton()
    private let sizeSlider = NSSlider()
    private let sizeLabel = NSTextField(labelWithString: "")
    private let sheetCheckbox = NSButton(checkboxWithTitle: "", target: nil, action: nil)
    private let resultLabel = NSTextField(wrappingLabelWithString: "")

    init(settings: PluginSettings, lastResult: String?, strings: @escaping (String) -> String,
         onClose: @escaping (PluginSettings) -> Void) {
        self.settings = settings
        self.strings = strings
        self.onClose = onClose
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 460, height: 220),
                              styleMask: [.titled, .closable], backing: .buffered, defer: true)
        window.title = strings("settings.title")
        window.isReleasedWhenClosed = false
        super.init(window: window)
        window.delegate = self
        buildContent(in: window)
        show(result: lastResult)
        window.center()
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) {
        fatalError("SettingsWindowController is made in code")
    }

    private func buildContent(in window: NSWindow) {
        nameField.stringValue = settings.roiName
        for shape in PluginSettings.ROIShape.allCases {
            shapePopUp.addItem(withTitle: strings("settings.roiKind.\(shape.rawValue)"))
            shapePopUp.lastItem?.representedObject = shape.rawValue
        }
        shapePopUp.selectItem(at: PluginSettings.ROIShape.allCases.firstIndex(of: settings.roiShape) ?? 0)
        sizeSlider.minValue = Double(PluginSettings.sizeRange.lowerBound)
        sizeSlider.maxValue = Double(PluginSettings.sizeRange.upperBound)
        sizeSlider.integerValue = settings.roiSizePercent
        sizeSlider.target = self
        sizeSlider.action = #selector(sizeChanged(_:))
        sizeLabel.stringValue = "\(settings.roiSizePercent)"
        sheetCheckbox.title = strings("settings.showSheet")
        sheetCheckbox.state = settings.showResultSheet ? .on : .off
        let done = NSButton(title: strings("settings.done"), target: self, action: #selector(done(_:)))
        done.keyEquivalent = "\r"

        let sizeRow = NSStackView(views: [sizeSlider, sizeLabel])
        let grid = NSGridView(views: [
            [NSTextField(labelWithString: strings("settings.roiName")), nameField],
            [NSTextField(labelWithString: strings("settings.roiKind")), shapePopUp],
            [NSTextField(labelWithString: strings("settings.roiSize")), sizeRow],
            [NSGridCell.emptyContentView, sheetCheckbox],
            [NSTextField(labelWithString: strings("settings.lastResult")), resultLabel],
        ])
        grid.column(at: 0).xPlacement = .trailing
        grid.rowAlignment = .firstBaseline
        nameField.widthAnchor.constraint(greaterThanOrEqualToConstant: 240).isActive = true
        resultLabel.preferredMaxLayoutWidth = 280

        let stack = NSStackView(views: [grid, done])
        stack.orientation = .vertical
        stack.alignment = .trailing
        stack.edgeInsets = NSEdgeInsets(top: 20, left: 20, bottom: 20, right: 20)
        window.contentView = stack
    }

    /// Shows the last result of a command in the window.
    func show(result: String?) {
        resultLabel.stringValue = result ?? strings("result.none")
    }

    @objc private func sizeChanged(_ sender: NSSlider) {
        sizeLabel.stringValue = "\(sender.integerValue)"
    }

    @objc private func done(_ sender: Any?) {
        window?.close()
    }

    func windowWillClose(_ notification: Notification) {
        let shapeName = shapePopUp.selectedItem?.representedObject as? String
        settings = PluginSettings(roiName: nameField.stringValue.isEmpty ? settings.roiName : nameField.stringValue,
                                  roiShape: shapeName.flatMap(PluginSettings.ROIShape.init(rawValue:)) ?? settings.roiShape,
                                  roiSizePercent: sizeSlider.integerValue,
                                  showResultSheet: sheetCheckbox.state == .on)
        onClose(settings)
    }
}

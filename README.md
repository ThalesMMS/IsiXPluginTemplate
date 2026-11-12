# MyIsiXPlugin — a template for IsiX DICOM Viewer plugins

This is an Xcode project that builds a plugin for IsiX DICOM Viewer with the
plugin API version 1: a `.isixplugin` bundle written in Swift against
**IsiXPluginKit**, the framework the application provides to its plugins. It
is a small working plugin, meant to be copied and changed:

- a **settings window** (menu *Plugins › Others › My IsiX Plugin Settings…*);
- **Inspect Image** (*Image Filters*): reads the frontmost viewer's current
  image, prints its geometry and measures its pixels in a background task;
- **Mark Centre** (*ROI Tools*): adds an oval or a rectangle around the centre
  of the image and asks the application for its statistics.

Without an open viewer the two viewer commands are disabled; if they run
anyway, they end with a message and touch nothing. Closing the viewer while a
measurement runs cancels it.

IsiX DICOM Viewer loads three plugin formats: `.isixplugin` bundles of plugin
API version 1, which this template builds, and plugins made for Horos or
OsiriX (`.horosplugin`, `.osirixplugin`), which load as they are, without
being rebuilt or changed. New plugins use plugin API version 1; an old plugin
whose source you have can also be ported to it (see
[Porting a Horos or OsiriX plugin](#porting-a-horos-or-osirix-plugin)).

## Requirements

- macOS 15 or later, Xcode 16 or later.
- The IsiXPluginKit SDK for plugin API version 1 (below).
- To run the plugin: an IsiX DICOM Viewer that supports plugin API version 1,
  on Apple silicon (`arm64`) or Intel (`x86_64`). The Mac App Store edition of
  the application does not load plugins.

## The SDK

The SDK is a folder with `IsiXPluginKit.framework` (the binary to link and one
`.swiftinterface` per architecture) and `SDK.json`, which says which API
version, kit version and source revision it is and records the SHA-256 of the
binary and of each interface. The project looks for it in `SDK/`.

This repository includes the SDK of **IsiX DICOM Viewer 6.0.0**, as published
with that release in `IsiXPluginKit-SDK-6.0.0.zip`:

| | |
|---|---|
| Plugin API version | 1 |
| Kit version | 1.0.0 |
| Source revision | `3d4e52fd9631f2d0783d6c727c4cedbc9900c1da` (tag `v6.0.0` of [IsiX-DICOM-Viewer-macOS](https://github.com/ThalesMMS/IsiX-DICOM-Viewer-macOS)) |
| Architectures | `arm64`, `x86_64` |
| SHA-256 of the binary | `56f796e41f265c5b8b0703e28740d3ad32490a825011c54f3e0390fbf9f25b02` |
| SHA-256 of `IsiXPluginKit-SDK-6.0.0.zip` | `89da5b1c51f9a4b4697b157d86c37252a0deb5ddbe9f8a9477b1a69b0ab53fef` |

Nothing needs to be downloaded to build the example: `script/sdk.py check`
confirms that what is in `SDK/` is that SDK.

### Updating the SDK

A plugin built against this SDK keeps loading in later releases of the
application that support plugin API version 1, so the SDK only needs to be
replaced to use what a later release of the kit adds. Each release of IsiX
DICOM Viewer publishes the SDK beside the application, as
`IsiXPluginKit-SDK-<version>.zip`; the release's `Verification.txt` lists the
SHA-256 of the zip. To put one in place:

    shasum -a 256 IsiXPluginKit-SDK-<version>.zip      # compare with Verification.txt
    unzip -q IsiXPluginKit-SDK-<version>.zip
    rm -rf SDK && mv IsiXPluginKit-SDK-<version> SDK
    script/sdk.py check --archs "arm64 x86_64" --require-porting

`check` compares the binary and the interfaces with the SHA-256 that `SDK.json`
records and prints the SDK's identity; `git status` then shows what the new
SDK changed. The SDK also carries `SDK/README.md`, the documentation of the
plugin API, and the application's licence files. An SDK for another plugin API
version is refused until `IsiXPluginAPIVersion` in `Config/Info.plist` says
that version too.

The SDK can also be made from the IsiX DICOM Viewer source, which exports it
with its `tools/export-isix-plugin-kit.py`:

    script/sdk.py export --app-source <IsiX DICOM Viewer source folder>

To keep the SDK elsewhere, put `ISIX_SDK_DIR = <folder>` in
`Config/SDK.local.xcconfig` (not tracked by Git), or pass `--sdk <folder>` to
`script/build.sh`.

The plugin **links** IsiXPluginKit but never carries a copy of it: at run time
it uses the application's. A second copy would give the plugin types the
application does not recognise, so the application refuses a bundle that
embeds one.

The SDK carries `SDK/LegacyPorting/`, from the same source revision: the
porting kit **IsiXLegacyBridge** and its scripts, for plugins written for
Horos or OsiriX (see [Porting a Horos or OsiriX plugin](#porting-a-horos-or-osirix-plugin)).
`SDK/LegacyPorting/PORTING.json` lists the SHA-256 of each of its files, and
`check` compares them.

## Building

In Xcode, open `MyIsiXPlugin.xcodeproj` and build the `MyIsiXPlugin` scheme.
From the command line:

    script/build.sh                              # Debug, this Mac's architecture
    script/build.sh --configuration Release      # Release, arm64 and x86_64
    script/build.sh --arch x86_64                # one architecture

The bundle is written to `build/Products/<configuration>/MyIsiXPlugin.isixplugin`
and signed ad hoc. The first build step checks the SDK and stops with the
reason if it is missing, for another API version or without an architecture
being built; it also writes the SDK's identity into the bundle, as
`Contents/Resources/IsiXPluginKitSDK.json`. After building, `script/build.sh`
runs `script/verify_bundle.py`, which reads the bundle the way the application
does before loading it and reports anything the application would refuse.
Building never installs the plugin nor starts the application: see
[Installing](#installing) and [Running and debugging](#running-and-debugging).

## Making your own plugin from the template

1. Copy this folder and its `script/` and `Config/` (the `build/` folder is not
   needed).
2. In `Config/MyIsiXPlugin.xcconfig`, set:
   - `PRODUCT_BUNDLE_IDENTIFIER`: your identifier, in reverse DNS
     (`org.example.my-filter`). The application tells plugins apart by it;
     keep it the same across versions.
   - `PRODUCT_NAME`: the bundle's and the Swift module's name.
   - `MARKETING_VERSION` and `CURRENT_PROJECT_VERSION`.
   - `ISIX_PLUGIN_ENTRY_CLASS`: the name of your entry class, if you rename it.
3. In `Config/Info.plist`, list the capabilities your plugin uses under
   `IsiXPluginCapabilities` (below). Leave `IsiXPluginAPIVersion` at `1`.
4. Change the code in `MyIsiXPlugin/Sources` and the resources in
   `MyIsiXPlugin/Resources`. Files added to these folders are part of the
   target without further steps.

You can rename the project, the target and the scheme in Xcode; if you do,
change `XCODE_PROJECT` and `SCHEME` at the top of `script/build.sh`.

## What the application expects

| Item | In this project |
| --- | --- |
| Extension `.isixplugin`, package type `BNDL` | `WRAPPER_EXTENSION = isixplugin`, `Config/Info.plist` |
| A Mach-O bundle | `MACH_O_TYPE = mh_bundle` |
| `IsiXPluginAPIVersion`, the integer `1` | `Config/Info.plist` |
| `CFBundleIdentifier`, `CFBundleName`, `CFBundleShortVersionString` | from the build settings above |
| `NSPrincipalClass`: the entry class, **qualified by its Swift module** | `$(PRODUCT_MODULE_NAME).$(ISIX_PLUGIN_ENTRY_CLASS)`, which builds to `MyIsiXPlugin.MyIsiXPlugin` |
| `IsiXPluginCapabilities`: what the plugin uses | `viewer.read`, `roi` |
| macOS 15.0 minimum | `MACOSX_DEPLOYMENT_TARGET = 15.0` |
| The architecture the application runs as | Debug: this Mac's; Release: `arm64` and `x86_64` |
| IsiXPluginKit linked from the SDK, not embedded; no `-undefined dynamic_lookup`; no bridging header unless the plugin carries a library ([below](#libraries-of-your-own)) | `FRAMEWORK_SEARCH_PATHS = $(ISIX_SDK_DIR)`, `OTHER_LDFLAGS = -framework IsiXPluginKit` |
| No symbol exported; code compiled with hidden visibility | `EXPORTED_SYMBOLS_FILE = Config/Exports.exp` (empty), `GCC_SYMBOLS_PRIVATE_EXTERN = YES` |

A bare class name in `NSPrincipalClass` (`MyIsiXPlugin` instead of
`MyIsiXPlugin.MyIsiXPlugin`) does not resolve, unless the class is declared
`@objc(MyIsiXPlugin)`.

Capabilities:

| String | Gives |
| --- | --- |
| `viewer.read` | viewers, their series, images, geometry and pixels; viewer events |
| `viewer.write` | committing pixels; setting the window level |
| `roi` | reading, creating, measuring and removing ROIs; ROI events |
| `database.read` | studies and series of the local database |
| `database.import` | importing files into the local database |

A call that needs a capability the plugin did not declare throws
`IsiXPluginError.capabilityNotDeclared`.

## How the example works

The code is in `MyIsiXPlugin/Sources`:

- `MyIsiXPlugin.swift` — the entry class. The application creates it once with
  `init()` and calls `activate(with:)` once, on the main actor; the plugin
  reads its resources, registers its commands with
  `context.register(_:handler:)` and observes `viewerWillClose`.
  `deactivate()` is called once before the application quits, after the
  application has cancelled the plugin's tasks and removed its commands and
  observations. If `activate` throws, the plugin is refused and everything it
  registered is removed.
- `ImageReport.swift` — what the commands compute, apart from the user
  interface: pixel statistics, a geometry summary, the centre ROI. Plain
  functions of the values the kit returns.
- `SettingsWindowController.swift` — the settings window, built in code. It is
  a regular window, not modal; closing it hands the settings back.
- `PluginSettings.swift` — the settings, read from `Defaults.plist`.

Things worth knowing when you change it:

- **Commands** are identified by their `id`; the title is only shown. A command
  with `requirement: .viewer` is disabled while no 2D viewer is open, and its
  invocation carries the frontmost viewer and a window to attach sheets to. An
  error the handler throws is shown to the user, with its description.
- **Everything the kit returns is a copy** the plugin owns — images, geometry,
  ROIs, statistics — valid after the viewer closes and readable on any thread.
  `IsiXViewer` is a handle that does not keep the viewer open: once the window
  closes, `isOpen` is false and its calls throw `IsiXPluginError.viewerClosed`.
- **Pixels** are `.float32` (one sample per pixel, in modality units) or
  `.rgba8`, by rows. **Geometry**: the centre of the pixel at column `c`, row `r`
  is `imagePosition + rowDirection * (c * columnSpacing) + columnDirection * (r * rowSpacing)`;
  `patientPoint(of:)` and `pixelPoint(of:)` convert, and the centre of the
  first pixel is (0, 0).
- **Long work** goes to `context.startTask(named:_:)`, which runs off the main
  actor; give it copies only, check `progress.isCancelled`, and hop back with
  `await MainActor.run`. The example cancels a measurement when its viewer
  closes.
- **Enumerations** of the kit may gain cases in later versions of API 1:
  handle `@unknown default`.
- **Resources** come from the plugin's own bundle: `context.bundle`. The
  example reads `Defaults.plist` and its menu titles and messages from
  `en.lproj/Localizable.strings`.
- **Settings** in the example last until the application quits. A plugin that
  keeps settings should store them in a file of its own rather than in the
  application's preferences.
- **Logging**: the example logs with `os.Logger`, subsystem = the bundle
  identifier, and keeps a journal of what it did that `description` returns.

## Libraries of your own

IsiX DICOM Viewer links ITK, VTK and DCMTK statically, but they are not part of
the plugin API: a plugin cannot use the application's copies, and their
version changes with the application. A plugin that needs ITK, DCMTK, VTK, GSL
or another C or C++ library **carries its own copy**, linked statically into
its executable, and keeps it apart from the application's copy, which is
loaded in the same process.

### Why the copies need to be kept apart

The application exports its symbols, among them the C++ templates and inline
functions of its libraries (`itk::Image<float, 3>::FillBuffer`, for example),
which are *weak definitions*. When a bundle that exports a weak definition of
the same name is loaded, macOS binds the bundle's calls to the application's
copy. A plugin built with ITK the usual way then runs the application's ITK
code on its own ITK objects: with two different versions of the library, that
is undefined behaviour. Two settings prevent it, and the template has both:

- **The plugin exports no symbol.** `EXPORTED_SYMBOLS_FILE` names
  `Config/Exports.exp`, which lists none. The application finds the entry class
  through the Objective-C runtime, not by symbol, so nothing is lost. This is
  the setting that matters: with it, even a library compiled with default
  visibility stays the plugin's own.
- **Code is compiled with hidden visibility**: `GCC_SYMBOLS_PRIVATE_EXTERN` and
  `GCC_INLINES_ARE_PRIVATE_EXTERN` for the plugin's own C, C++ and
  Objective-C, and `-fvisibility=hidden -fvisibility-inlines-hidden` for the
  library (below).

Ordinary functions and data are kept apart by the two-level namespace macOS
uses by default: never link with `-flat_namespace` or `-undefined
dynamic_lookup`. Objective-C classes are not covered by any of this: the
runtime keeps one class per name, so an Objective-C library the plugin carries
must not share class names with the application or with other plugins; give
its classes a prefix of your own.

### Building a library for the plugin

`script/build-library.py` builds ITK, DCMTK or any CMake project as one static
archive with every symbol hidden, for macOS 15, without taking anything from
Homebrew, MacPorts or `/usr/local`. It downloads nothing: extract the
library's release archive (for ITK, `InsightToolkit-<version>.tar.gz` from the
ITK releases on GitHub; for DCMTK, its source from dicom.offis.de or GitHub)
and point it there:

    script/build-library.py itk   --source ~/src/InsightToolkit-5.4.7
    script/build-library.py dcmtk --source ~/src/dcmtk-3.6.9
    script/build-library.py cmake --name GSL --source ~/src/gsl   # any CMake project

| Option | |
| --- | --- |
| `--arch arm64`, `x86_64` or `universal` | the architectures; this Mac's by default. A Release build of the plugin has both, so build the library with `--arch universal` for it |
| `--module NAME` (repeat) | the ITK or DCMTK modules to build. ITK by default: image filters, thresholding, smoothing, region growing, connected components, curvature flow, level sets, transforms, optimizers, registration, statistics, morphology. DCMTK by default: `ofstd`, `oflog`, `oficonv`, `dcmdata` (reading and writing DICOM files) |
| `--cmake-arg -DNAME=VALUE` (repeat) | more CMake options |
| `--output DIR` | where to install; `Libraries/` by default (ignored by Git) |

It installs `Libraries/<Name>/include`, `Libraries/<Name>/lib/lib<Name>.a`,
the library's licence files in `Libraries/<Name>/share/licenses` and
`Libraries/<Name>/library.json`, which records the version, the
architectures, the CMake options and how many symbols of the archive are not
hidden (for ITK 5.4, only a few vtables of the C++ standard library). For ITK
it also turns `ITK_TEMPLATE_VISIBILITY_DEFAULT` off, which otherwise forces
ITK's templates to default visibility. ITK 5.4 with the default modules takes
about a minute and a half on an Apple silicon Mac with four jobs.

A library without CMake, such as GSL from its release archive, is built with
the same flags by hand:

    CFLAGS="-fvisibility=hidden -mmacosx-version-min=15.0 -arch arm64" \
        ./configure --disable-shared --prefix="$PWD/../Libraries/GSL" && make install

### Using it in the plugin

1. Copy `Config/Libraries.xcconfig.example` to `Config/Libraries.xcconfig`,
   which `Config/MyIsiXPlugin.xcconfig` includes, and keep the lines of the
   libraries you built. It adds their headers (as system headers, so their
   warnings stay out of your build), their archives and `-lz -lc++`.
2. Write the code that uses the library in Objective-C++ (`.mm`) or C++
   (`.cpp`) files in `MyIsiXPlugin/Sources`, behind a C or Objective-C
   interface, and declare that interface in
   `MyIsiXPlugin/Sources/Libraries-Bridging-Header.h`; Swift calls it from
   there. Swift does not import the library's C++ headers.
3. Build. `script/verify_bundle.py` warns if the executable still exports weak
   definitions.
4. Distribute the library's licence files with the plugin, as its licence
   requires (ITK: Apache 2.0 with its `NOTICE`; DCMTK: BSD-style), for example
   by adding `Libraries/<Name>/share/licenses` to the bundle's resources.

### Size

Only the parts of the archive the plugin uses are linked. Measured on Apple
silicon, Release, `arm64` only (a universal bundle is about twice as large):

| | Archive | Plugin executable | Bundle |
| --- | --- | --- | --- |
| The template, no library | — | 178 KB | 196 KB |
| ITK 5.4.7, default modules, region growing on a 3D float image | 23.9 MB | 712 KB | 716 KB |
| DCMTK 3.7.0 (`ofstd`, `oflog`, `oficonv`, `dcmdata`), a dataset written and read in memory | 10.2 MB | 6.3 MB | 6.2 MB |

Most of the DCMTK plugin is the data dictionary and the character set tables,
which the recipe builds into the library so that the plugin needs no file of
DCMTK at run time.

The application refuses neither size nor library; it checks only that the
plugin carries no copy of IsiXPluginKit.

## Porting a Horos or OsiriX plugin

IsiX DICOM Viewer loads `.horosplugin` and `.osirixplugin` bundles as they
are, beside the `.isixplugin` ones: an old plugin needs no port to run. A
plugin whose source you have can still be ported to plugin API version 1, to
keep developing it against IsiXPluginKit, whose contract stays stable across
the releases that support version 1: `SDK/LegacyPorting/` holds
**IsiXLegacyBridge**, a kit that gives the old source the classes it was
written against (`PluginFilter`, `ViewerController`, `DCMPix`, `ROI`, the
database and network classes) rebuilt on IsiXPluginKit, and the converter that
turns the plugin's folder into a project of plugin API version 1 built with
that kit. The kit is compiled into the plugin; the result is an ordinary
`.isixplugin`. `SDK/LegacyPorting/IsiXLegacyBridge/README.md` explains the kit
and what it covers. Part of the kit derives from Horos and OsiriX: those files
keep their headers, and `SDK/LegacyPorting/LICENSE` carries their notice. A
ported plugin compiles the kit in, so keep that notice with it.

    # an Xcode project, written to <output>/<Module>
    python3 SDK/LegacyPorting/tools/port-legacy-plugin.py --source <old plugin folder> --output <folder> --sdk SDK

    # or convert and build in one step, without Xcode
    python3 SDK/LegacyPorting/tools/build-isix-legacy-plugin.py --sdk SDK --source <old plugin folder> \
        --output <folder> --arch arm64

The legacy folder is only read. The converted project keeps every file as it
was in `Porting/original/`, and `Porting/REPORT.md` lists each automatic
rewrite and what has no automatic equivalent, with its file and line. Code
that uses OpenGL or QuickTime is not covered. Keep the old plugin's licence
and credits with the port.

## Installing

The application loads plugins from its plugins folder when it starts:

| Folder | For |
| --- | --- |
| `~/Library/Application Support/IsiX/Plugins/` | the current user: the default, no administrator password |
| `/Library/Application Support/IsiX/Plugins/` | all users of the Mac, only when you ask for it |

Quit IsiX DICOM Viewer, then:

    script/build.sh --configuration Release
    script/install.py install                  # build/Products/Release/MyIsiXPlugin.isixplugin
    script/install.py install <bundle>         # any .isixplugin
    script/install.py where                    # print the folder

and start the application again: a running application keeps the version it
loaded until it quits. `--system` installs for all users; the script never asks
for administrator rights, so that works only if you may already write to that
folder. `--home <folder>` installs under another home folder, such as a
disposable one for tests.

What `install` does, and what it refuses:

- It checks the bundle the way the application will before loading it (the
  same checks as `script/verify_bundle.py`) and refuses one the application
  would refuse, or one whose signature is broken.
- It copies the bundle into a staging folder next to the destination, checks
  the copy, and puts it in place in one rename. If the script is interrupted,
  the installed plugin is either the old version or the new one, whole; the
  next run finishes or undoes what was left.
- It never replaces a bundle with another identifier, nor installs a second
  copy of the same identifier under another name: uninstall that one first.
- It refuses to replace a copy that a running process has loaded.
- The version it replaces is kept in `.horos-plugin-previous/` in the plugins
  folder, where the application itself keeps the previous version of a plugin
  it updates. `script/install.py restore` puts it back. If the new version
  stops the application while it starts, the application offers to restore it;
  once the application has loaded the new version, it discards the previous
  one.
- It writes nothing outside that plugins folder: other plugins, the disabled
  plugins folder and the folders of other applications are left as they are.
  A plugins folder that is a symbolic link is refused.

`script/install.py uninstall` removes the bundle named after `PRODUCT_NAME` (or
the one you name), and its previous version, only if it is a `.isixplugin`
whose identifier is `PRODUCT_BUNDLE_IDENTIFIER` (or `--identifier`). The
Plugins window of the application can also disable, enable and remove plugins.

## Running and debugging

Debug a plugin in the **development application**, which the IsiX DICOM Viewer
source builds and prepares with its `script/build_and_run.sh` (in its
`build/Development` folder). Run that way, the application keeps a private
database, and its plugins folders, crash marker and records live in a test
folder of their own: the plugins and data of the installed application are
neither read nor written. Only the development application honours this
isolation; `script/run-isolated.sh` refuses any other.

    script/build.sh
    script/run-isolated.sh --app <development application>          # run
    script/run-isolated.sh --app <development application> --lldb   # under LLDB

`ISIX_APP` can stand for `--app`. The test folder is `build/IsiXTestRoot` (or
`--test-root`). The plugin is loaded with `--LoadPlugin <bundle>`, by default
`build/Products/Debug/MyIsiXPlugin.isixplugin`, or another with `--bundle`.
With `--installed`, no bundle is named and the application loads only the
plugins installed in the test folder's own plugins folder: install into it
with

    script/install.py install --configuration Debug --home "build/IsiXTestRoot/Isolated Plugins/User"

The arguments are the ones the application's development script passes:
`-DATABASELOCATIONURL`, `-DEFAULT_DATABASELOCATIONURL` and
`-WebPortalDatabasePath` in the test folder, `-STORESCP NO`, no automatic
cleaning, routing or update checks, and `-IsolatedPluginsFolder <test
folder>/Isolated Plugins`.

**LLDB.** The application runs with the hardened runtime, so the debugger can
run it only if it is signed with `com.apple.security.get-task-allow`: prepare
it with the application source's `script/build_and_run.sh --debug`, which adds
that entitlement to the development application only. Set breakpoints in the
plugin's code by file and line: they stay pending until the application loads
the plugin, and resolve then. In the template, line 70 of `MyIsiXPlugin.swift`
is in `activate(with:)`:

    script/run-isolated.sh --app <development application> --lldb \
        --lldb-command 'breakpoint set -f MyIsiXPlugin.swift -l 70' --lldb-command run

When it stops there, `bt` shows the application's loader calling the plugin,
`po self` prints the plugin's journal, and `continue` goes on.

**Xcode.** The `MyIsiXPlugin` scheme runs with *Ask on Launch*: choose the
development application when Xcode asks. The scheme passes the same isolation
arguments, with the test folder in `build/IsiXTestRoot` and `--LoadPlugin` of
the bundle just built.

The plugin's `os.Logger` messages use the bundle identifier as subsystem:

    log stream --predicate 'subsystem == "com.example.myisixplugin"'

## When the application refuses a plugin

The application reads the bundle before it runs any of its code. When it
refuses one, the Plugins window shows the state (*Incompatible*, *Blocked*,
*Load failed*) and the reason, which also goes to the application's log. For
example:

| Cause | Reason given |
| --- | --- |
| `IsiXPluginAPIVersion` 2 | The plugin needs plugin API version 2; this application supports up to version 1. … |
| Built for `x86_64` only, application running as `arm64` | The plugin is built for x86_64; this application runs as arm64. … |
| `NSPrincipalClass` without the module | The entry class MyIsiXPlugin was not found in the plugin. Name it ModuleName.ClassName in NSPrincipalClass. |
| A copy of IsiXPluginKit inside the bundle | The bundle carries its own copy of IsiXPluginKit.framework. … |
| A key of the former plugin API (`pluginType`, `MenuTitles`, …) | The bundle declares keys of the former plugin API … |
| A file changed after signing | The plugin signature is invalid: a sealed resource is missing or invalid. … (*Blocked*) |
| No signature (`arm64`) | The bundle “…” couldn’t be loaded. (*Load failed*) |

`script/verify_bundle.py <bundle>` reports the same problems without starting
the application. If `activate(with:)` throws, the plugin is refused with the
error's description.

## Signing and distribution

`script/build.sh` signs the bundle ad hoc, which is enough on the Mac that
built it. The application checks that a plugin's signature is intact before
loading it, and refuses a bundle that changed after it was signed; `arm64`
code must be signed to load at all. It accepts plugins signed by other
developers. To give the plugin to others, sign it with your Developer ID
certificate:

    codesign --force --timestamp --sign "Developer ID Application: <you>" MyIsiXPlugin.isixplugin

and have it notarized, so that Gatekeeper accepts it once downloaded. Never
switch Gatekeeper off nor change the application's signature to make a plugin
load. Distribute the Release build, which has both architectures; the plugin
API, the SDK and the application's checks are the same on both.

## Testing the scripts

    python3 tests/test_install.py

checks install, update, interrupted updates, restore and uninstall in
disposable home folders whose names have spaces and accents, and that other
plugins and the folders of other applications are left untouched. It needs a
Debug build of the plugin and exits with 2 (skipped) without one.

    python3 tests/test_sdk_porting.py [--app-source <IsiX DICOM Viewer source folder>]

checks how `script/sdk.py` copies the porting kit into the SDK and refuses a
copy that differs from `PORTING.json`; with `--app-source` it also runs the
converter from such a copy. With an SDK in `SDK/`, it checks too that a copy
whose binary or interface differs from the SHA-256 in `SDK.json` is refused.

## License

The template is distributed under the MIT License (`LICENSE`).
© 2026 Thales Matheus M Santos (ThalesMMS). A plugin made from it can have any
license, open or proprietary: the MIT License asks only that its copyright and
permission notice stay with what was copied from the template.

The SDK in `SDK/` is part of IsiX DICOM Viewer and keeps the application's
license, the GNU Lesser General Public License, version 3
(`SDK/COPYING.LESSER`, which builds on `SDK/COPYING`); `SDK/LICENSE` and
`SDK/NOTICE` are the application's license notice and credits. A plugin
carries no copy of IsiXPluginKit: it links the framework that is inside the
application, which that license allows for a plugin under any license.

Part of the porting kit in `SDK/LegacyPorting/` derives from Horos and OsiriX:
those files keep their headers, and `SDK/LegacyPorting/LICENSE` carries their
notice. A ported plugin compiles the kit in, so that license's terms for the
kit go with it.

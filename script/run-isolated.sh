#!/bin/bash
# Runs the IsiX DICOM Viewer development application, isolated, with this plugin.
#
#   script/run-isolated.sh --app <IsiX DICOM Viewer Development.app> [options] [-- more application arguments]
#
#   --app APP          the application to run; or set ISIX_APP. It must be the
#                      development application (identifier ending in
#                      .local-development), the only one that keeps its plugins
#                      in the isolated folder.
#   --bundle BUNDLE    the plugin to load with --LoadPlugin; by default
#                      build/Products/Debug/<PRODUCT_NAME>.isixplugin.
#   --installed        load no bundle by path: only what is installed in the
#                      isolated plugins folder (see README.md).
#   --test-root DIR    the isolated database and plugins folder; by default
#                      build/IsiXTestRoot in this project.
#   --lldb             run the application under LLDB. The application must be
#                      signed with com.apple.security.get-task-allow.
#   --lldb-command C   with --lldb, an LLDB command to run after loading the
#                      application (repeatable); "run" starts it.
#   --lldb-batch       with --lldb, run the commands and quit LLDB when they
#                      are done or the application stops, for scripted checks.
#
# The arguments are the ones the application's own development script passes:
# a private database in the test root, no DICOM listener, no automatic
# cleaning, routing or update checks, and the plugins folders inside the test
# root. Quitting the application ends the run.
set -euo pipefail
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PRODUCT_NAME="$(sed -n 's/^PRODUCT_NAME *= *//p' "$PROJECT_DIR/Config/MyIsiXPlugin.xcconfig")"
APP="${ISIX_APP:-}"
BUNDLE="$PROJECT_DIR/build/Products/Debug/$PRODUCT_NAME.isixplugin"
LOAD=1
TEST_ROOT="$PROJECT_DIR/build/IsiXTestRoot"
DEBUGGER=0
LLDB_COMMANDS=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        --app) APP="$2"; shift 2 ;;
        --bundle) BUNDLE="$2"; shift 2 ;;
        --installed) LOAD=0; shift ;;
        --test-root) TEST_ROOT="$2"; shift 2 ;;
        --lldb) DEBUGGER=1; shift ;;
        --lldb-command) LLDB_COMMANDS+=(-o "$2"); shift 2 ;;
        --lldb-batch) LLDB_COMMANDS=(--batch ${LLDB_COMMANDS[@]+"${LLDB_COMMANDS[@]}"}); shift ;;
        --) shift; break ;;
        -h|--help) sed -n '2,27p' "$0"; exit 0 ;;
        *) echo "unknown option $1" >&2; exit 2 ;;
    esac
done
if [[ -z "$APP" || ! -d "$APP" ]]; then
    echo "Give the development application with --app or ISIX_APP." >&2
    exit 2
fi
IDENTIFIER="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$APP/Contents/Info.plist")"
if [[ "$IDENTIFIER" != *.local-development ]]; then
    echo "$APP is not the development application ($IDENTIFIER): it would read and write the real plugins folders." >&2
    echo "Build the development application from the IsiX DICOM Viewer source (script/build_and_run.sh)." >&2
    exit 2
fi
EXECUTABLE="$APP/Contents/MacOS/$(/usr/libexec/PlistBuddy -c 'Print :CFBundleExecutable' "$APP/Contents/Info.plist")"
mkdir -p "$TEST_ROOT"
TEST_ROOT="$(cd "$TEST_ROOT" && pwd)"
ARGS=(-hideListenerError NO -moveToApplicationsFolderAlertSuppress YES
      -DATABASELOCATION 1 -DATABASELOCATIONURL "$TEST_ROOT" -DEFAULT_DATABASELOCATION 1 -DEFAULT_DATABASELOCATIONURL "$TEST_ROOT"
      -WebPortalDatabasePath "$TEST_ROOT/WebUsers.sql" -AUTOCLEANINGSPACE NO -AUTOCLEANINGDATE NO -AUTOROUTINGACTIVATED NO
      -STORESCP NO -USESTORESCP NO -checkForUpdatesPlugins NO -SUEnableAutomaticChecks NO
      -IsolatedPluginsFolder "$TEST_ROOT/Isolated Plugins")
if [[ $LOAD == 1 ]]; then
    if [[ ! -d "$BUNDLE" ]]; then
        echo "No plugin at $BUNDLE; build it with script/build.sh." >&2
        exit 2
    fi
    ARGS+=(--LoadPlugin "$(cd "$(dirname "$BUNDLE")" && pwd)/$(basename "$BUNDLE")")
fi
ARGS+=("$@")
# Keep the application's temporary files in the user's own temporary folder.
export TMPDIR="$(/usr/bin/getconf DARWIN_USER_TEMP_DIR)"
echo "Isolated test root: $TEST_ROOT"
if [[ $DEBUGGER == 1 ]]; then
    if ! /usr/bin/codesign -d --entitlements - "$APP" 2>/dev/null | grep -q 'get-task-allow'; then
        echo "$APP is not signed for debugging (com.apple.security.get-task-allow); LLDB cannot run it." >&2
        echo "Prepare it with the application source's script/build_and_run.sh --debug." >&2
        exit 2
    fi
    exec /usr/bin/xcrun lldb ${LLDB_COMMANDS[@]+"${LLDB_COMMANDS[@]}"} -- "$EXECUTABLE" "${ARGS[@]}"
fi
exec "$EXECUTABLE" "${ARGS[@]}"

#!/bin/bash
# Builds MyIsiXPlugin.isixplugin into build/Products/<configuration>/ and checks it.
#
#   script/build.sh [--configuration Debug|Release] [--arch arm64|x86_64|universal] [--sdk DIR]
#
# Debug builds this Mac's architecture and Release both, unless --arch says
# otherwise. --sdk (or ISIX_SDK_DIR) chooses the SDK folder; by default the
# project's SDK/. Building never installs the plugin nor starts the
# application: script/install.py and script/run-isolated.sh do that.
set -euo pipefail
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# The project and scheme; change them here if you rename them. The bundle's
# name comes from PRODUCT_NAME in Config/MyIsiXPlugin.xcconfig.
XCODE_PROJECT="$PROJECT_DIR/MyIsiXPlugin.xcodeproj"
SCHEME=MyIsiXPlugin
PRODUCT_NAME="$(sed -n 's/^PRODUCT_NAME *= *//p' "$PROJECT_DIR/Config/MyIsiXPlugin.xcconfig")"
CONFIGURATION=Debug
ARCH=""
SDK_DIR="${ISIX_SDK_DIR:-$PROJECT_DIR/SDK}"
while [[ $# -gt 0 ]]; do
    case "$1" in
        --configuration) CONFIGURATION="$2"; shift 2 ;;
        --arch) ARCH="$2"; shift 2 ;;
        --sdk) SDK_DIR="$2"; shift 2 ;;
        -h|--help) sed -n '2,9p' "$0"; exit 0 ;;
        *) echo "unknown option $1" >&2; exit 2 ;;
    esac
done
case "$CONFIGURATION" in Debug|Release) ;; *) echo "--configuration must be Debug or Release" >&2; exit 2 ;; esac
case "$ARCH" in
    "") if [[ "$CONFIGURATION" == Debug ]]; then ARCHS="$(uname -m)"; else ARCHS="arm64 x86_64"; fi ;;
    arm64|x86_64) ARCHS="$ARCH" ;;
    universal) ARCHS="arm64 x86_64" ;;
    *) echo "--arch must be arm64, x86_64 or universal" >&2; exit 2 ;;
esac
[[ "$SDK_DIR" = /* ]] || SDK_DIR="$PWD/$SDK_DIR"
/usr/bin/python3 "$PROJECT_DIR/script/sdk.py" check --sdk "$SDK_DIR" --archs "$ARCHS"
mkdir -p "$PROJECT_DIR/build/logs"
LOG="$PROJECT_DIR/build/logs/build-$CONFIGURATION.log"
if ! xcodebuild -project "$XCODE_PROJECT" -scheme "$SCHEME" -configuration "$CONFIGURATION" \
        -destination 'generic/platform=macOS' -derivedDataPath "$PROJECT_DIR/build/DerivedData" \
        SYMROOT="$PROJECT_DIR/build/Products" OBJROOT="$PROJECT_DIR/build/Intermediates" \
        ISIX_SDK_DIR="$SDK_DIR" ARCHS="$ARCHS" ONLY_ACTIVE_ARCH=NO build > "$LOG" 2>&1; then
    grep -E 'error:|warning:' "$LOG" | sort -u | tail -40 >&2 || true
    echo "Build failed. Log: $LOG" >&2
    exit 1
fi
grep -E '(^|[^a-z])warning:' "$LOG" | grep -v -e 'Run script build phase' -e 'appintentsmetadataprocessor' | sort -u >&2 || true
BUNDLE="$PROJECT_DIR/build/Products/$CONFIGURATION/$PRODUCT_NAME.isixplugin"
/usr/bin/python3 "$PROJECT_DIR/script/verify_bundle.py" "$BUNDLE" --archs "$ARCHS"
echo "$BUNDLE"

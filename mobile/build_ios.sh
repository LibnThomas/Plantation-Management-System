#!/usr/bin/env bash
# iPhone app - needs a Mac with Xcode (free from the App Store).
#
#   ./build_ios.sh              run it in the iPhone Simulator on this Mac      (no Apple ID / signing)
#   ./build_ios.sh ipa          make mobile/dist/plantation-ms-unsigned.ipa      (no signing here; install it
#                               with Sideloadly or AltStore, which sign it with your free Apple ID)
#   ./build_ios.sh xcode        open the Xcode project: choose your Apple ID team and your iPhone, press Run
#
# Apple only lets signed apps onto a real iPhone. With a free Apple ID the app must be re-installed every
# 7 days; with a paid Apple Developer account ($99/year) it lasts a year and can go out via TestFlight.
source "$(dirname "$0")/_common.sh"

if [ "$(uname)" != "Darwin" ] || ! xcode-select -p >/dev/null 2>&1; then
  echo "This needs a Mac with Xcode installed (App Store > Xcode, then open it once)." >&2
  exit 1
fi

if [ -d build/pms_mobile/ios ]; then
  briefcase update iOS --update-requirements --update-resources
else
  briefcase create iOS
fi

project=$(ls -d build/pms_mobile/ios/xcode/*.xcodeproj | head -1)
scheme=$(basename "$project" .xcodeproj)

case "${1:-sim}" in
  sim)
    briefcase build iOS
    briefcase run iOS            # pick an iPhone model when asked
    ;;
  ipa)
    rm -rf build/ipa
    xcodebuild -project "$project" -scheme "$scheme" -configuration Release \
      -sdk iphoneos -destination 'generic/platform=iOS' -derivedDataPath build/ipa \
      CODE_SIGNING_ALLOWED=NO CODE_SIGNING_REQUIRED=NO CODE_SIGN_IDENTITY="" build
    app=$(ls -d build/ipa/Build/Products/Release-iphoneos/*.app | head -1)
    rm -rf build/ipa/Payload && mkdir -p build/ipa/Payload
    cp -R "$app" build/ipa/Payload/
    (cd build/ipa && rm -f ../../dist/plantation-ms-unsigned.ipa && zip -qry ../../dist/plantation-ms-unsigned.ipa Payload)
    echo
    echo "Done: mobile/dist/plantation-ms-unsigned.ipa"
    echo "Install it on your iPhone with Sideloadly (sideloadly.io) or AltStore (altstore.io) and your Apple ID."
    ;;
  xcode)
    open "$project"
    echo "In Xcode: click the project > Signing & Capabilities > Team = your Apple ID; choose your iPhone at the top; press Run."
    ;;
  *)
    echo "Use: ./build_ios.sh [sim|ipa|xcode]" >&2
    exit 1
    ;;
esac

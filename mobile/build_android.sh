#!/usr/bin/env bash
# Builds the Android app:  mobile/dist/plantation-ms.apk
#
#   ./build_android.sh          build the .apk
#   ./build_android.sh run      build and start it on a connected phone (USB debugging) or the emulator
#
# The first build downloads Java, the Android SDK and the Python packages (about 2 GB, 10-20 minutes)
# and asks you to accept Google's Android SDK licence (type y). Later builds take 1-3 minutes.
source "$(dirname "$0")/_common.sh"

if [ -d build/pms_mobile/android ]; then
  briefcase update android --update-requirements --update-resources
else
  briefcase create android
fi
briefcase build android

if [ "${1:-}" = "run" ]; then
  briefcase run android
  exit 0
fi

briefcase package android --packaging-format debug-apk
apk=$(ls -t dist/*.apk | head -1)
cp "$apk" dist/plantation-ms.apk
echo
echo "Done: mobile/dist/plantation-ms.apk"
echo "Send it to the phone (WhatsApp / USB / Google Drive), open it and allow 'Install unknown apps'."

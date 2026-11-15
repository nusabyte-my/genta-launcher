#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "$0")/.." && pwd)"
SDK="${ANDROID_SDK_ROOT:-$ROOT/.tools/android}"
JAR="$(find "$SDK" -name android.jar -type f -print -quit)"
AAPT="$(find "$SDK" -name aapt2 -type f -print -quit)"
if [[ -z "$JAR" || -z "$AAPT" ]]; then
  printf 'Android SDK platform and build tools are missing. See README.md.\n' >&2
  exit 1
fi
TOOLS="$(dirname -- "$AAPT")"
BUILD="$ROOT/android/build"
mkdir -p "$BUILD/classes" "$BUILD/generated" "$BUILD/dex" "$ROOT/dist" "$ROOT/android/assets/locales" "$ROOT/android/assets/sounds"
cp "$ROOT"/locales/*.json "$ROOT/android/assets/locales/"
cp "$ROOT/locales/languages.json" "$ROOT/android/assets/languages.json"
cp "$ROOT"/assets/sounds/*.wav "$ROOT/android/assets/sounds/"
"$AAPT" compile --dir "$ROOT/android/res" -o "$BUILD/resources.zip"
"$AAPT" link -o "$BUILD/base.apk" -I "$JAR" --manifest "$ROOT/android/AndroidManifest.xml" --java "$BUILD/generated" --min-sdk-version 26 --target-sdk-version 36 --version-code 2 --version-name 0.2.0 -A "$ROOT/android/assets" -0 wav "$BUILD/resources.zip"
find "$ROOT/android/src" "$BUILD/generated" -name '*.java' -print > "$BUILD/sources.txt"
javac --release 8 -encoding UTF-8 -classpath "$JAR" -d "$BUILD/classes" @"$BUILD/sources.txt"
jar --create --file "$BUILD/classes.jar" -C "$BUILD/classes" .
"$TOOLS/d8" --lib "$JAR" --min-api 26 --output "$BUILD/dex" "$BUILD/classes.jar"
cp "$BUILD/base.apk" "$BUILD/unsigned.apk"
(cd "$BUILD/dex" && zip -q -u "$BUILD/unsigned.apk" classes*.dex)
"$TOOLS/zipalign" -f -p 4 "$BUILD/unsigned.apk" "$BUILD/aligned.apk"
KEY="$ROOT/.tools/android/genta-development.jks"
if [[ ! -f "$KEY" ]]; then
  keytool -genkeypair -keystore "$KEY" -storepass android -keypass android -alias genta-development -keyalg RSA -keysize 2048 -validity 3650 -dname 'CN=Genta Development, O=Genta, C=MY' -noprompt
  chmod 600 "$KEY"
fi
"$TOOLS/apksigner" sign --ks "$KEY" --ks-key-alias genta-development --ks-pass pass:android --key-pass pass:android --out "$ROOT/dist/genta-android.apk" "$BUILD/aligned.apk"
"$TOOLS/apksigner" verify --verbose "$ROOT/dist/genta-android.apk"
printf 'Built and signature-verified: %s/dist/genta-android.apk\n' "$ROOT"

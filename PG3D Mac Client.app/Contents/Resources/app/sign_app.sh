#!/bin/zsh
# Signs the client with a local signing identity that stays the same across updates.
#
# macOS remembers privacy permissions (Screen Recording, Downloads, ...) by the app's
# code signature. An ad-hoc signature changes with every build, so every update looked
# like a new app: permissions were asked again and adding the app in System Settings
# didn't match the running one. A self-signed certificate created once on this Mac (in
# its own keychain, never sent anywhere) gives every version the same identity.
#
# Usage: sign_app.sh <path to PG3D Mac Client.app>
set -u
APP="$1"
SUPPORT="$HOME/Library/Application Support/PG3D Mac Client"
DIR="$SUPPORT/signing"
KC="$DIR/pg3d-signing.keychain-db"
PASS="pg3d-local-signing"   # protects only this throwaway keychain
NAME="PG3D Mac Client Local Signing"
BUNDLE_ID="local.pg3d.macclient"
mkdir -p "$DIR"

identity() {
  /usr/bin/security find-identity -p codesigning "$KC" 2>/dev/null | /usr/bin/awk -v n="\"$NAME\"" 'index($0, n) {print $2; exit}'
}

create_identity() {
  local tmp; tmp="$(/usr/bin/mktemp -d)"
  cat > "$tmp/cfg" <<EOF
[req]
distinguished_name = dn
prompt = no
[dn]
CN = $NAME
[ext]
basicConstraints = critical, CA:false
keyUsage = critical, digitalSignature
extendedKeyUsage = critical, codeSigning
EOF
  /usr/bin/openssl req -x509 -newkey rsa:2048 -nodes -days 7300 -config "$tmp/cfg" -extensions ext \
      -keyout "$tmp/key.pem" -out "$tmp/cert.pem" >/dev/null 2>&1 || { rm -rf "$tmp"; return 1; }
  /usr/bin/openssl pkcs12 -export -inkey "$tmp/key.pem" -in "$tmp/cert.pem" -name "$NAME" \
      -out "$tmp/id.p12" -passout "pass:$PASS" >/dev/null 2>&1 || { rm -rf "$tmp"; return 1; }
  rm -f "$KC"
  /usr/bin/security create-keychain -p "$PASS" "$KC" >/dev/null 2>&1 || { rm -rf "$tmp"; return 1; }
  /usr/bin/security set-keychain-settings "$KC" >/dev/null 2>&1   # no auto-lock
  /usr/bin/security unlock-keychain -p "$PASS" "$KC" >/dev/null 2>&1
  /usr/bin/security import "$tmp/id.p12" -k "$KC" -P "$PASS" -T /usr/bin/codesign >/dev/null 2>&1 || { rm -rf "$tmp"; return 1; }
  /usr/bin/security set-key-partition-list -S apple-tool:,apple:,codesign: -s -k "$PASS" "$KC" >/dev/null 2>&1
  rm -rf "$tmp"
  [[ -n "$(identity)" ]]
}

[[ -n "$(identity)" ]] || create_identity || echo "sign_app: could not create the local signing identity"
/usr/bin/security unlock-keychain -p "$PASS" "$KC" >/dev/null 2>&1
ID="$(identity)"
/usr/bin/xattr -cr "$APP" 2>/dev/null
if [[ -n "$ID" ]] && /usr/bin/codesign --force --keychain "$KC" -s "$ID" -i "$BUNDLE_ID" "$APP" 2>&1; then
  echo "sign_app: signed with $NAME"
  exit 0
fi
# Fallback: ad-hoc (works, but macOS may ask for permissions again after updates).
/usr/bin/codesign --force -s - -i "$BUNDLE_ID" "$APP" 2>&1 && echo "sign_app: signed ad-hoc" && exit 2
exit 1

#!/usr/bin/env bash
set -e

echo ">>> Installing uv"
pip install --user --quiet uv

echo ">>> Installing Google Cloud CLI"
if [ ! -d "$HOME/google-cloud-sdk" ]; then
  curl -sSL https://sdk.cloud.google.com | bash -s -- --disable-prompts --install-dir="$HOME" > /tmp/gcloud-install.log 2>&1
fi
grep -q "google-cloud-sdk/path.bash.inc" ~/.bashrc || \
  echo 'source "$HOME/google-cloud-sdk/path.bash.inc"' >> ~/.bashrc

echo ">>> Setup complete"

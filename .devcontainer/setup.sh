cd /workspaces/Multi-Agent-Creative-Studio
cat > .devcontainer/setup.sh << 'EOF'
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

echo ">>> Installing git-lfs"
sudo apt-get update -qq && sudo apt-get install -y -qq git-lfs && git lfs install

echo ">>> Setup complete"
EOF
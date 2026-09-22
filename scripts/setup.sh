#!/usr/bin/env bash
# Create the vaudeville user config directory.
# Run once after installing the plugin.
set -euo pipefail

CONFIG_DIR="${HOME}/.vaudeville"
mkdir -p "${CONFIG_DIR}"

if [ ! -f "${CONFIG_DIR}/config" ]; then
  echo "[vaudeville] Create ${CONFIG_DIR}/config to set default_model, providers, and commands."
else
  echo "[vaudeville] Config already exists at ${CONFIG_DIR}/config"
fi
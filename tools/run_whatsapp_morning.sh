#!/bin/bash
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
LOG_FILE="$PROJECT_DIR/.tmp/whatsapp_morning.log"
mkdir -p "$PROJECT_DIR/.tmp"
echo "--- $(date '+%Y-%m-%d %H:%M:%S') ---" >> "$LOG_FILE"
cd "$PROJECT_DIR" && python3 tools/generate_whatsapp_digest.py --section morning >> "$LOG_FILE" 2>&1
echo "" >> "$LOG_FILE"

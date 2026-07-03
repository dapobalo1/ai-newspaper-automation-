#!/bin/bash
# Wrapper for cron: runs process_link_requests.py with logging.
# Cron does not inherit your shell environment, so paths are set explicitly here.

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
LOG_FILE="$PROJECT_DIR/.tmp/link_requests.log"

mkdir -p "$PROJECT_DIR/.tmp"

echo "--- $(date '+%Y-%m-%d %H:%M:%S') ---" >> "$LOG_FILE"
cd "$PROJECT_DIR" && python3 tools/process_link_requests.py >> "$LOG_FILE" 2>&1
echo "" >> "$LOG_FILE"

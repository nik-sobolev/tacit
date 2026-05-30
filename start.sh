#!/bin/bash
source /Users/nsobolev/.zshrc 2>/dev/null || source /Users/nsobolev/.bash_profile 2>/dev/null || true
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$PATH"
export PYTHONPATH=/Users/nsobolev/Library/Python/3.9/lib/python/site-packages
cd /Users/nsobolev/Documents/tacit/backend
exec /usr/bin/python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8000

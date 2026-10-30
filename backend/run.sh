#!/usr/bin/env bash
cd "$(dirname "$0")"

if [ ! -d "venv" ]; then
  python3 -m venv venv
  ./venv/bin/pip install --upgrade pip
  ./venv/bin/pip install -r requirements.txt
fi

echo "🚀 Запуск Worker Control Hub на http://localhost:8000"
./venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

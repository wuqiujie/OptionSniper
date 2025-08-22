#!/bin/bash
# run.sh - 一键启动 Sell Put Checker

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"

# 激活虚拟环境
if [ -f "$PROJECT_DIR/.venv/bin/activate" ]; then
    source "$PROJECT_DIR/.venv/bin/activate"
else
    echo "❌ 没找到虚拟环境 .venv，请先创建: python -m venv .venv"
    exit 1
fi

# 关键：让 Python 能从 src/ 下找到 sellput_checker 包
export PYTHONPATH="$PROJECT_DIR/src:$PYTHONPATH"

# 可选：保存即热重载
# export STREAMLIT_SERVER_RUN_ON_SAVE=true

# 启动
exec streamlit run "$PROJECT_DIR/src/sellput_checker/app.py"
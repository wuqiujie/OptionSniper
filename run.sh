#!/bin/bash
# run.sh - 一键安装/更新环境并启动 OptionSniper (Sell Put Checker)
# 用法：
#   ./run.sh            # 若无 .venv 会自动创建，安装依赖并启动
#   ./run.sh --recreate # 删除并重建 .venv（当环境坏掉时用）
#   ./run.sh --no-run   # 只安装/更新，不启动 Streamlit

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$PROJECT_DIR/.venv"
PYTHON_BIN="python3"
REQUIREMENTS_FILE="$PROJECT_DIR/requirements.txt"
APP_ENTRY="$PROJECT_DIR/src/sellput_checker/app.py"

info()  { echo -e "\033[1;34m[INFO]\033[0m $*"; }
success(){ echo -e "\033[1;32m[DONE]\033[0m $*"; }
warn()  { echo -e "\033[1;33m[WARN]\033[0m $*"; }
die()   { echo -e "\033[1;31m[ERR ]\033[0m $*"; exit 1; }

# 处理参数
RECREATE=0
NO_RUN=0
for arg in "${@:-}"; do
  case "$arg" in
    --recreate) RECREATE=1 ;;
    --no-run)   NO_RUN=1 ;;
    "-") ;; # 忽略
    "--") ;; # 忽略
    "")  ;; # 忽略
    *) warn "未知参数: $arg" ;;
  esac
done

# 检查 Python 可用
command -v "$PYTHON_BIN" >/dev/null 2>&1 || die "未找到 $PYTHON_BIN，请先安装（macOS 可用: brew install python）。"

# 若指定 --recreate 则删除旧环境
if [[ $RECREATE -eq 1 && -d "$VENV_DIR" ]]; then
  info "删除旧虚拟环境: $VENV_DIR"
  rm -rf "$VENV_DIR"
fi

# 创建虚拟环境（如不存在）
if [[ ! -d "$VENV_DIR" ]]; then
  info "创建虚拟环境: $VENV_DIR"
  "$PYTHON_BIN" -m venv "$VENV_DIR" || die "创建虚拟环境失败。"
fi

# 激活虚拟环境
# shellcheck disable=SC1090
source "$VENV_DIR/bin/activate" || die "激活虚拟环境失败。"

# 升级安装工具
info "升级 pip/setuptools/wheel"
python -m pip install --upgrade pip setuptools wheel

# 安装项目依赖
if [[ -f "$REQUIREMENTS_FILE" ]]; then
  info "安装依赖: $REQUIREMENTS_FILE"
  pip install -r "$REQUIREMENTS_FILE"
else
  warn "未找到 requirements.txt，将仅确保 Streamlit 可用。"
  pip install --upgrade streamlit
fi


success "依赖安装完成"

# 确保已安装 Streamlit（requirements.txt 里可能没写）
info "检查 Streamlit 可用性"
if ! command -v streamlit >/dev/null 2>&1; then
  warn "未检测到 streamlit 命令，安装中..."
  pip install --upgrade streamlit
fi

# 打印调试信息
info "Python: $(which python)"
info "Pip:    $(which pip)"
info "Streamlit 版本: $(python -c 'import importlib,sys;\n\nmod=importlib.util.find_spec("streamlit");\nprint("missing" if mod is None else __import__("streamlit").__version__)' 2>/dev/null || echo missing)"

# 让 Python 能从 src/ 下找到 sellput_checker 包
export PYTHONPATH="$PROJECT_DIR/src:${PYTHONPATH:-}"

# 可选：保存即热重载
# export STREAMLIT_SERVER_RUN_ON_SAVE=true

# 启动应用（除非 --no-run）
if [[ $NO_RUN -eq 0 ]]; then
  [[ -f "$APP_ENTRY" ]] || die "未找到应用入口: $APP_ENTRY"
  info "启动 Streamlit 应用..."
  exec python -m streamlit run "$APP_ENTRY"
else
  success "环境已就绪（按需使用: source $VENV_DIR/bin/activate）"
fi
#!/bin/bash
cd "$(dirname "$0")"
if [ -f .pid ]; then
    PID=$(cat .pid)
    if ps -p $PID > /dev/null 2>&1; then
        kill $PID
        echo "服务已停止，PID: $PID"
        rm .pid
    else
        echo "进程不存在，清理PID文件"
        rm .pid
    fi
else
    echo "未找到PID文件，尝试查找进程..."
    pkill -f "python main.py --webui-only"
    echo "已尝试停止所有相关进程"
fi

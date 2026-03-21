#!/bin/bash
cd "$(dirname "$0")"
echo "=== Daily Stock Analysis 系统状态 ==="
echo ""
if [ -f .pid ]; then
    PID=$(cat .pid)
    if ps -p $PID > /dev/null 2>&1; then
        echo "✅ 服务运行中"
        echo "   PID: $PID"
        echo "   访问地址: http://127.0.0.1:8001"
        echo ""
        echo "端口监听:"
        lsof -i :8001 2>/dev/null || echo "   未检测到端口监听"
    else
        echo "❌ 服务未运行（PID文件存在但进程不存在）"
    fi
else
    echo "❌ 服务未运行"
fi
echo ""
echo "最近日志:"
tail -5 logs/stock_analysis_$(date +%Y%m%d).log 2>/dev/null || echo "   无日志文件"

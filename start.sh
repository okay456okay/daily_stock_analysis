#!/bin/bash
cd "$(dirname "$0")"
source venv/bin/activate
export http_proxy=http://127.0.0.1:7890
export https_proxy=http://127.0.0.1:7890
nohup python main.py --webui-only > logs/service.log 2> logs/service_error.log &
echo $! > .pid
echo "服务已启动，PID: $(cat .pid)"
echo "访问地址: http://127.0.0.1:8001"

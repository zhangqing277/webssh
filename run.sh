#!/bin/bash
# 网络设备管理平台 - 启动脚本

cd "$(dirname "$0")"

# 检查端口是否被占用
PORT=5000
PID=$(lsof -t -i:$PORT 2>/dev/null)
if [ -n "$PID" ]; then
    echo "端口 $PORT 已被占用 (PID: $PID)，正在停止..."
    kill -9 $PID 2>/dev/null
    sleep 1
fi

# 确保日志目录存在
mkdir -p logs

echo "========================================"
echo "  网络设备管理平台"
echo "  访问地址: http://0.0.0.0:5000"
echo "  默认账号: admin / admin123"
echo "========================================"

python3 app.py

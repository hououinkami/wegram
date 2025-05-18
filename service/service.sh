#!/bin/zsh

# 脚本路径配置 - 请修改为实际路径
cd "$(dirname "$0")"
WX2TG_PATH="./wx2tg.py"
TG2WX_PATH="./tg2wx.py"
LOG_DIR="./logs"

# 创建日志目录（如果不存在）
mkdir -p $LOG_DIR

# 获取当前日期时间作为日志文件名后缀
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")

# 日志文件路径
WX2TG_LOG="$LOG_DIR/wx2tg.log"
TG2WX_LOG="$LOG_DIR/tg2wx.log"

# 改进的进程检测函数 - 解决grep自匹配问题
check_process_running() {
    local script_name=$1
    
    # 使用grep -v grep排除grep自身
    if ps aux | grep "$script_name" | grep -v grep > /dev/null; then
        return 0  # 进程正在运行
    fi
    
    return 1  # 进程不存在
}

# 获取进程PID
get_process_pid() {
    local script_name=$1

    # 通过进程名查找
    local pid=$(ps aux | grep "$script_name" | grep -v grep | awk '{print $2}' | head -n 1)
    if [ -n "$pid" ]; then
        echo $pid
        return
    fi
}

# 启动进程并保存PID
start_process() {
    local script_path=$1
    local log_file=$2
    local script_name=$(basename $script_path)
    
    # 启动进程并记录PID
    nohup python3 $script_path > $log_file 2>&1 &
    local pid=$!
    
    echo "进程已启动，PID: $pid"
    return 0
}

# 停止进程
stop_process() {
    local script_name=$1
    
    # 获取PID
    local pid=$(get_process_pid $script_name)
    
    if [ -n "$pid" ]; then
        echo "尝试终止PID为 $pid 的进程..."
        kill $pid 2>/dev/null
        sleep 2
        
        # 检查进程是否仍在运行
        if ps -p $pid > /dev/null 2>&1; then
            echo "进程仍在运行，尝试强制终止..."
            kill -9 $pid 2>/dev/null
            sleep 1
        fi
    else
        echo "没有找到运行中的 $script_name 进程"
    fi
}

# 检查并管理wx2tg服务
manage_wx2tg() {
    local script_name=$(basename $WX2TG_PATH)
    echo "检查 $script_name 服务状态..."
    
    # 检查进程是否存在
    if check_process_running $script_name; then
        echo "$script_name 服务进程存在，准备重启..."
        stop_process $script_name
    else
        echo "$script_name 服务未运行，准备启动..."
    fi
    
    # 启动服务
    echo "启动 $script_name 服务..."
    start_process $WX2TG_PATH $WX2TG_LOG
    
    # 等待服务初始化
    echo "等待服务初始化..."
    sleep 5
    
    # 获取PID
    local pid=$(get_process_pid $script_name)
    
    # 检查服务是否正常运行
    if [ -n "$pid" ] && ps -p $pid > /dev/null 2>&1; then
        # 检查日志中是否有错误信息
        if grep -i "error\|exception\|failed\|traceback" $WX2TG_LOG > /dev/null; then
            echo "⚠️ $script_name 进程已启动，但日志中包含错误信息:"
            grep -i -A 3 -B 1 "error\|exception\|failed\|traceback" $WX2TG_LOG | head -n 10
            echo "完整日志路径: $WX2TG_LOG"
        else
            echo "✅ $script_name 服务已成功启动，PID: $pid"
            echo "日志保存在: $WX2TG_LOG"
        fi
    else
        echo "❌ $script_name 服务启动失败，查看错误日志:"
        cat $WX2TG_LOG | tail -n 15
    fi
}

# 检查并管理tg2wx服务
manage_tg2wx() {
    local script_name=$(basename $TG2WX_PATH)
    echo "检查 $script_name 服务状态..."
    
    # 检查进程是否存在
    if check_process_running $script_name; then
        echo "$script_name 服务进程存在，准备重启..."
        stop_process $script_name
    else
        echo "$script_name 服务未运行，准备启动..."
    fi
    
    # 启动服务
    echo "启动 $script_name 服务..."
    start_process $TG2WX_PATH $TG2WX_LOG
    
    # 等待服务初始化
    echo "等待服务初始化..."
    sleep 5
    
    # 获取PID
    local pid=$(get_process_pid $script_name)
    
    # 检查服务是否正常运行
    if [ -n "$pid" ] && ps -p $pid > /dev/null 2>&1; then
        # 检查日志中是否有错误信息
        if grep -i "error\|exception\|failed\|traceback" $TG2WX_LOG > /dev/null; then
            echo "⚠️ $script_name 进程已启动，但日志中包含错误信息:"
            grep -i -A 3 -B 1 "error\|exception\|failed\|traceback" $TG2WX_LOG | head -n 10
            echo "完整日志路径: $TG2WX_LOG"
        else
            echo "✅ $script_name 服务已成功启动，PID: $pid"
            echo "日志保存在: $TG2WX_LOG"
        fi
    else
        echo "❌ $script_name 服务启动失败，查看错误日志:"
        cat $TG2WX_LOG | tail -n 15
    fi
}

# 显示服务状态和日志摘要
show_status() {
    echo "\n当前服务状态:"
    
    local wx2tg_name=$(basename $WX2TG_PATH)
    local tg2wx_name=$(basename $TG2WX_PATH)
    
    # 获取wx2tg PID
    local wx2tg_pid=$(get_process_pid $wx2tg_name)
    
    if [ -n "$wx2tg_pid" ] && ps -p $wx2tg_pid > /dev/null 2>&1; then
        echo "✅ $wx2tg_name 服务进程正在运行，PID: $wx2tg_pid"
        ps -p $wx2tg_pid -o pid,ppid,user,%cpu,%mem,start,time,command
        if [ -f "$WX2TG_LOG" ]; then
            echo "最新日志内容:"
            tail -n 5 $WX2TG_LOG
        fi
    else
        echo "❌ $wx2tg_name 服务未运行"
    fi
    
    echo ""
    
    # 获取tg2wx PID
    local tg2wx_pid=$(get_process_pid $tg2wx_name)
    
    if [ -n "$tg2wx_pid" ] && ps -p $tg2wx_pid > /dev/null 2>&1; then
        echo "✅ $tg2wx_name 服务进程正在运行，PID: $tg2wx_pid"
        ps -p $tg2wx_pid -o pid,ppid,user,%cpu,%mem,start,time,command
        if [ -f "$TG2WX_LOG" ]; then
            echo "最新日志内容:"
            tail -n 5 $TG2WX_LOG
        fi
    else
        echo "❌ $tg2wx_name 服务未运行"
    fi
}

# 主函数
main() {
    echo "===== 消息转发服务管理 ====="
    echo "开始时间: $(date)"
    
    # 管理服务
    manage_wx2tg
    echo ""
    manage_tg2wx
    
    # 显示最终状态
    show_status
    
    echo "\n操作完成: $(date)"
    echo "=========================="
}

# 执行主函数
main

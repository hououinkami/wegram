#!/bin/zsh

# ps -ef | grep main.py

# 脚本路径配置 - 请修改为实际路径
MAIN_PATH="./main.py"
LOG_DIR="./logs"

# 创建日志目录（如果不存在）
mkdir -p $LOG_DIR

# 获取当前日期时间作为日志文件名后缀
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")

# 按日期生成日志文件
LOG_FILE="$LOG_DIR/$(date +'%Y-%m-%d').log"

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
    
    # 切换到脚本所在目录
    cd $(dirname $script_path)
    
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
        
        # 再次检查进程是否已停止
        if ps -p $pid > /dev/null 2>&1; then
            echo "❌ 进程终止失败"
            return 1
        else
            echo "✅ 进程已成功终止"
            return 0
        fi
    else
        echo "没有找到运行中的 $script_name 进程"
        return 0
    fi
}

# 检查并管理main服务
manage_main() {
    local script_name=$(basename $MAIN_PATH)
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
    start_process $MAIN_PATH $LOG_FILE
    
    # 等待服务初始化
    echo "等待服务初始化..."
    sleep 5
    
    # 获取PID
    local pid=$(get_process_pid $script_name)
    
    # 检查服务是否正常运行
    if [ -n "$pid" ] && ps -p $pid > /dev/null 2>&1; then
        # 检查日志中是否有错误信息
        if grep -i "error\|exception\|failed\|traceback" $LOG_FILE > /dev/null; then
            echo "⚠️ $script_name 进程已启动，但日志中包含错误信息:"
            grep -i -A 3 -B 1 "error\|exception\|failed\|traceback" $LOG_FILE | head -n 10
            echo "完整日志路径: $LOG_FILE"
        else
            echo "✅ $script_name 服务已成功启动，PID: $pid"
            echo "日志保存在: $LOG_FILE"
        fi
    else
        echo "❌ $script_name 服务启动失败，查看错误日志:"
        cat $LOG_FILE | tail -n 15
    fi
}

# 只启动服务
start_main() {
    local script_name=$(basename $MAIN_PATH)
    echo "启动 $script_name 服务..."
    
    # 检查进程是否已经在运行
    if check_process_running $script_name; then
        local pid=$(get_process_pid $script_name)
        echo "⚠️ $script_name 服务已在运行，PID: $pid"
        echo "如需重启，请使用 restart 选项"
        return 1
    fi
    
    # 启动服务
    start_process $MAIN_PATH $LOG_FILE
    
    # 等待服务初始化
    echo "等待服务初始化..."
    sleep 5
    
    # 获取PID
    local pid=$(get_process_pid $script_name)
    
    # 检查服务是否正常运行
    if [ -n "$pid" ] && ps -p $pid > /dev/null 2>&1; then
        # 检查日志中是否有错误信息
        if grep -i "error\|exception\|failed\|traceback" $LOG_FILE > /dev/null; then
            echo "⚠️ $script_name 进程已启动，但日志中包含错误信息:"
            grep -i -A 3 -B 1 "error\|exception\|failed\|traceback" $LOG_FILE | head -n 10
            echo "完整日志路径: $LOG_FILE"
        else
            echo "✅ $script_name 服务已成功启动，PID: $pid"
            echo "日志保存在: $LOG_FILE"
        fi
    else
        echo "❌ $script_name 服务启动失败，查看错误日志:"
        cat $LOG_FILE | tail -n 15
    fi
}

# 只停止服务
stop_main() {
    local script_name=$(basename $MAIN_PATH)
    echo "停止 $script_name 服务..."
    
    if check_process_running $script_name; then
        stop_process $script_name
    else
        echo "$script_name 服务未运行"
    fi
}

# 显示服务状态和日志摘要
show_status() {
    echo "\n当前服务状态:"
    
    local main_name=$(basename $MAIN_PATH)
    
    # 获取main PID
    local main_pid=$(get_process_pid $main_name)
    
    if [ -n "$main_pid" ] && ps -p $main_pid > /dev/null 2>&1; then
        echo "✅ $main_name 服务进程正在运行，PID: $main_pid"
        ps -p $main_pid -o pid,ppid,user,%cpu,%mem,start,time,command
        if [ -f "$LOG_FILE" ]; then
            echo "最新日志内容:"
            tail -n 5 $LOG_FILE
        fi
    else
        echo "❌ $main_name 服务未运行"
    fi
}

# 显示帮助信息
show_help() {
    echo "===== 消息转发服务管理脚本 ====="
    echo "用法: $0 [选项]"
    echo ""
    echo "选项:"
    echo "  start     - 启动服务（如果已运行则提示）"
    echo "  stop      - 停止服务"
    echo "  restart   - 重启服务（默认行为）"
    echo "  status    - 显示服务状态"
    echo "  help      - 显示此帮助信息"
    echo ""
    echo "无参数运行时默认执行重启操作"
    echo "================================"
}

# 主函数
main() {
    local action=${1:-restart}  # 默认为restart
    
    echo "===== 消息转发服务管理 ====="
    echo "开始时间: $(date)"
    echo "执行操作: $action"
    echo ""
    
    case $action in
        "start")
            start_main
            show_status
            ;;
        "stop")
            stop_main
            show_status
            ;;
        "restart")
            manage_main
            show_status
            ;;
        "status")
            show_status
            ;;
        "help"|"-h"|"--help")
            show_help
            return 0
            ;;
        *)
            echo "❌ 未知选项: $action"
            echo ""
            show_help
            return 1
            ;;
    esac
    
    echo "\n操作完成: $(date)"
    echo "=========================="
}

# 执行主函数，传递所有命令行参数
main "$@"
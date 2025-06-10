# 使用官方 Python 3.11 slim 镜像作为基础镜像
FROM python:3.11-slim

# 设置工作目录
WORKDIR /app

# 安装系统依赖、编译工具和 ffmpeg
RUN apt-get update && apt-get install -y \
    ffmpeg \
    gcc \
    g++ \
    make \
    libffi-dev \
    libssl-dev \
    && rm -rf /var/lib/apt/lists/*

# 设置时区（可选）
ENV TZ=Asia/Shanghai

# 升级 pip 和安装 wheel
RUN pip install --upgrade pip setuptools wheel

# 复制 requirements.txt 并安装 Python 依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制项目文件
COPY . .

# 暴露端口
EXPOSE 8088

# 启动主服务
CMD ["python", "main.py"]

# docker build -t wegram:kami .
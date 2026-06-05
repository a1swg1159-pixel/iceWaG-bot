FROM python:3.11-slim

WORKDIR /app

# 换国内源加速
RUN sed -i 's/deb.debian.org/mirrors.aliyun.com/g' /etc/apt/sources.list.d/debian.sources 2>/dev/null || \
    sed -i 's/deb.debian.org/mirrors.aliyun.com/g' /etc/apt/sources.list

# Pillow 中文渲染
RUN apt-get update && apt-get install -y --no-install-recommends \
    fonts-noto-cjk fonts-noto-color-emoji fonts-wqy-microhei \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/

COPY bot.py pyproject.toml ./
COPY src/ src/

CMD ["python", "bot.py"]

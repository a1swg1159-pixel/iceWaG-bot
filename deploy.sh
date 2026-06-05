#!/bin/bash
# ================================================
# QQ Bot 一键部署（Bot + NapCat Docker）
# 用法: bash deploy.sh
# ================================================
set -e

clear
echo "╔══════════════════════════════════════╗"
echo "║     QQ Bot 一键部署                  ║"
echo "╚══════════════════════════════════════╝"
echo ""

# ====== 0. 检查 Docker ======
if ! command -v docker &>/dev/null; then
    echo "📦 Docker 未安装，正在自动安装..."
    curl -fsSL https://get.docker.com | bash
    systemctl enable docker
    systemctl start docker
    echo "✅ Docker 安装完成"
fi

if ! docker compose version &>/dev/null 2>&1; then
    echo "📦 安装 docker-compose-plugin..."
    apt-get update -qq && apt-get install -y -qq docker-compose-plugin
    echo "✅ docker compose 安装完成"
fi

# Docker 权限检查
if ! docker ps &>/dev/null 2>&1; then
    echo "🔧 当前用户无 Docker 权限，正在添加..."
    sudo usermod -aG docker "$USER"
    echo "⚠ 权限已添加，请重新登录后再次运行 bash deploy.sh"
    echo "   或者现在执行: newgrp docker && bash deploy.sh"
    exit 0
fi

# ====== 1. 读取默认值（从本地 .env）======
if [ -f ".env" ]; then
    DEF_TOKEN=$(grep -oP 'ONEBOT_V11_ACCESS_TOKEN=\K.*' .env | head -1)
    DEF_GROUPS=$(grep -oP 'DAILY_PUSH_GROUPS=\K.*' .env | head -1)
    DEF_CITY=$(grep -oP 'WEATHER_CITY=\K.*' .env | head -1)
    DEF_ADMIN=$(grep -oP 'ADMIN_QQ=\K.*' .env | head -1)
fi
DEF_TOKEN=${DEF_TOKEN:-$(openssl rand -hex 8)}
DEF_CITY=${DEF_CITY:-北京}

# ====== 2. 收集配置 ======
echo "📋 请填写以下配置（回车使用本地 .env 值）"
echo ""

read -p "QQ 号: " QQ_ACCOUNT
while [ -z "$QQ_ACCOUNT" ]; do
    read -p "QQ 号（必填）: " QQ_ACCOUNT
done

read -p "Access Token [$DEF_TOKEN]: " ACCESS_TOKEN
ACCESS_TOKEN=${ACCESS_TOKEN:-$DEF_TOKEN}

read -p "推送目标群号 [$DEF_GROUPS]: " PUSH_GROUPS
PUSH_GROUPS=${PUSH_GROUPS:-$DEF_GROUPS}

read -p "默认天气城市 [$DEF_CITY]: " WEATHER_CITY
WEATHER_CITY=${WEATHER_CITY:-$DEF_CITY}

read -p "管理员 QQ 号 [$DEF_ADMIN]: " ADMIN_QQ
while [ -z "$ADMIN_QQ" ]; do
    read -p "管理员 QQ 号（必填）: " ADMIN_QQ
done

# ====== 3. 生成 .env ======
echo ""
echo "📝 生成配置文件..."

cat > .env << EOF
ENVIRONMENT=production
HOST=0.0.0.0
PORT=8081
ONEBOT_V11_ACCESS_TOKEN=$ACCESS_TOKEN
DAILY_PUSH_GROUPS=$PUSH_GROUPS
WEATHER_CITY=$WEATHER_CITY
ADMIN_QQ=$ADMIN_QQ
EOF
echo "   .env ✓"

# ====== 4. 生成 NapCat OneBot 配置 ======
mkdir -p napcat/config

cat > "napcat/config/onebot11_${QQ_ACCOUNT}.json" << EOF
{
  "network": {
    "websocketClients": [
      {
        "enable": true,
        "name": "bot",
        "url": "ws://bot:8081/onebot/v11/ws",
        "reportSelfMessage": false,
        "messagePostFormat": "array",
        "token": "$ACCESS_TOKEN",
        "reconnectInterval": 30000
      }
    ]
  }
}
EOF
echo "   napcat/config/onebot11_${QQ_ACCOUNT}.json ✓"

# ====== 5. 构建并启动 ======
echo ""
echo "🚀 构建并启动服务..."

docker compose up -d --build

echo ""
echo "╔══════════════════════════════════════╗"
echo "║     ✅ 部署完成！                    ║"
echo "╠══════════════════════════════════════╣"
echo "║                                      ║"
echo "║  下一步：扫码登录 QQ                  ║"
echo "║                                      ║"
echo "║  浏览器打开 WebUI:                    ║"
echo "║  http://$(hostname -I 2>/dev/null | awk '{print $1}' || echo '服务器IP'):6099/webui/ ║"
echo "║                                      ║"
echo "║  1. 点击「登录」                      ║"
echo "║  2. 手机 QQ 扫码                      ║"
echo "║  3. 完成！Bot 自动开始工作            ║"
echo "║                                      ║"
echo "╠══════════════════════════════════════╣"
echo "║  常用命令:                            ║"
echo "║  docker compose logs -f bot          ║"
echo "║  docker compose restart             ║"
echo "║  docker compose down && docker compose up -d ║"
echo "╚══════════════════════════════════════╝"

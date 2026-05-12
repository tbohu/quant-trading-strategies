#!/bin/bash
# 旺财AI量化分析系统 V7.0 一键部署
set -e
echo "======================================="
echo "  旺财AI量化系统 V7.0 一键部署"
echo "======================================="

apt-get update -q && apt-get install -y -q python3 python3-pip python3-venv nginx curl

mkdir -p /var/www/wangcai/static
cd /var/www/wangcai
python3 -m venv venv
source venv/bin/activate
pip install -q fastapi uvicorn[standard] python-dotenv requests pydantic schedule

cat > /var/www/wangcai/.env << 'ENV'
APP_ACCESS_TOKEN=Wangcai_1dcRzE-OSUOcRhrp99ZjxiWeyzxR2cHwnyQPWNhAnpQ
DEEPSEEK_API_KEY=sk-5ad660c9a8bb40c6bb621049a2b41a0b
OPENAI_API_KEY=
CORS_ORIGINS=http://120.76.41.74,http://cailaiwang352.top,http://www.cailaiwang352.top
DB_PATH=/var/www/wangcai/wangcai.db
TRUST_PROXY_HEADERS=false
WECOM_WEBHOOK=
EMAIL_SMTP_HOST=smtp.163.com
EMAIL_SMTP_PORT=465
EMAIL_SMTP_SSL=true
ENV

cat > /etc/systemd/system/wangcai.service << 'SVC'
[Unit]
Description=旺财AI量化分析系统V7
After=network.target
[Service]
Type=simple
User=root
WorkingDirectory=/var/www/wangcai
Environment=PATH=/var/www/wangcai/venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin
ExecStart=/var/www/wangcai/venv/bin/uvicorn server:app --host 127.0.0.1 --port 7777 --workers 1
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal
[Install]
WantedBy=multi-user.target
SVC

cat > /etc/nginx/sites-available/wangcai << 'NGINX'
server {
    listen 80;
    server_name 120.76.41.74 cailaiwang352.top www.cailaiwang352.top;
    client_max_body_size 20M;
    location / {
        proxy_pass http://127.0.0.1:7777;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_read_timeout 120s;
    }
}
NGINX

ln -sf /etc/nginx/sites-available/wangcai /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl restart nginx
systemctl daemon-reload && systemctl enable wangcai && systemctl restart wangcai
sleep 5

if curl -s http://localhost:7777/health | grep -q "ok"; then
    echo "======================================="
    echo "  ✅ 部署成功！"
    echo "  访问：http://120.76.41.74"
    echo "  令牌：Wangcai_1dcRzE-OSUOcRhrp99ZjxiWeyzxR2cHwnyQPWNhAnpQ"
    echo "======================================="
else
    echo "❌ 启动失败：journalctl -u wangcai -n 30 --no-pager"
fi

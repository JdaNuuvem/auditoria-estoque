#!/bin/bash
# Auditoria de Estoque - Deploy no Coolify
# Execute no terminal do Coolify (conectado ao localhost)

set -e

echo "=== Auditoria de Estoque ==="
echo "Criando diretorios..."
mkdir -p /opt/auditoria/templates

# Requirements
cat > /opt/auditoria/requirements.txt << 'REQEOF'
flask==3.1.3
flask-cors==6.0.5
requests==2.32.3
REQEOF

# Server (use base64 to avoid paste corruption)
echo "Escrevendo server.py..."
python3 -c "
import base64
srv_b64 = open('/tmp/srv64.txt').read().replace('\n','')
open('/opt/auditoria/server.py','wb').write(base64.b64decode(srv_b64))
print('server.py OK')
"

echo "Escrevendo index.html..."
python3 -c "
import base64
html_b64 = open('/tmp/html64.txt').read().replace('\n','')
open('/opt/auditoria/templates/index.html','wb').write(base64.b64decode(html_b64))
print('index.html OK')
"

# Dockerfile
cat > /opt/auditoria/Dockerfile << 'DEOF'
FROM python:3.13-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY server.py .
COPY templates/ templates/
ENV DATA_DIR=/data
VOLUME ["/data"]
EXPOSE 5000
CMD ["python", "server.py"]
DEOF

echo "Construindo imagem Docker..."
cd /opt/auditoria
docker build -t auditoria .

echo "Parando container antigo se existir..."
docker stop auditoria 2>/dev/null || true
docker rm auditoria 2>/dev/null || true

# /opt/auditoria/data e montado como volume: cache de produtos, sessoes de
# auditoria e usuarios cadastrados sobrevivem a rebuilds/redeploys do container.
mkdir -p /opt/auditoria/data

echo "Iniciando container..."
docker run -d --name auditoria --restart unless-stopped \
  -p 5000:5000 \
  -v /opt/auditoria/data:/data \
  auditoria

echo ""
echo "=== DEPLOY CONCLUIDO ==="
echo "Acesse: http://177.7.45.242:5000/"
echo ""
echo "Se nao acessivel, verifique o firewall:"
echo "  ufw allow 5000/tcp"
echo "  ou iptables -A INPUT -p tcp --dport 5000 -j ACCEPT"

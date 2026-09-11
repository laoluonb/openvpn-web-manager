server {
    listen __WEB_PORT__ ssl;
    server_name _;
    server_tokens off;

    ssl_certificate /etc/openvpn-manager/tls/server.crt;
    ssl_certificate_key /etc/openvpn-manager/tls/server.key;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_session_cache shared:OpenVPNManagerTLS:10m;
    ssl_session_timeout 1d;
    ssl_session_tickets off;

    root /opt/openvpn-web-manager/web;
    index index.html;

    add_header X-Content-Type-Options "nosniff" always;
    add_header X-Frame-Options "DENY" always;
    add_header Referrer-Policy "no-referrer" always;
    add_header Permissions-Policy "camera=(), microphone=(), geolocation=()" always;
    add_header Content-Security-Policy "default-src 'self'; connect-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'" always;

    location /api/ {
        proxy_pass http://127.0.0.1:9090;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-Proto https;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_connect_timeout 5s;
        proxy_read_timeout 130s;
        proxy_send_timeout 30s;
        proxy_buffering off;
    }

    location / {
        try_files $uri $uri/ /index.html;
    }

    location ~ /\. {
        deny all;
    }
}

# 部署手册

## 前置条件

- Docker 20.10+ 与 Docker Compose v2
- 可用内存建议 ≥ 1 GB（仅内置引擎）；启用 PaddleOCR 建议 ≥ 4 GB
- 磁盘：每页图片约 200–800 KB，按学期预留 2–10 GB

## 标准部署

```bash
cd deploy
cp .env.example .env
```

生成密钥并写入 `.env`：

```bash
openssl rand -hex 32
```

```env
SECRET_KEY=<粘贴生成的 64 位十六进制>
```

启动：

```bash
docker compose up -d --build
docker compose logs -f mistakebook
```

访问 `http://<主机IP>:8080`，注册首个账号（自动成为管理员）。

## 各平台说明

### Linux

`DATA_DIR` 建议使用绝对路径，例如 `/srv/mistakebook/data`。
若宿主挂载目录权限严格，确认 UID `10001` 可写：

```bash
sudo chown -R 10001:10001 /srv/mistakebook/data
```

### Windows Docker Desktop

默认 `DATA_DIR=./data` 即可。若放在网络驱动器，性能会明显下降，建议放本地磁盘。

### 群晖 Synology

1. Container Manager → 项目 → 新增，或直接在 SSH 中使用 `docker compose`。
2. 使用共享文件夹子目录作为数据目录：
   ```env
   DATA_DIR=/volume1/docker/mistakebook/data
   ```
3. 若出现 Permission denied，在 File Station 中给该目录读写权限，或用 SSH 执行：
   ```bash
   sudo chown -R 10001:10001 /volume1/docker/mistakebook/data
   ```
4. 低端型号（ARM、内存 < 2 GB）建议保持 `OCR_PROVIDER=heuristic`，避免加载重型模型。

### 威联通 QNAP

与群晖类似，共享目录通常为 `/share/CACHEDEV1_DATA/...`：

```env
DATA_DIR=/share/CACHEDEV1_DATA/Container/mistakebook/data
```

### 绿联 / 极空间

两者均支持 Docker Compose。将 `DATA_DIR` 指向其容器数据目录下的子路径即可；
若 UI 只允许图形化创建，可把 `docker-compose.yml` 内容粘贴为项目配置。

## 启用 PostgreSQL

```bash
docker compose --profile postgres up -d
```

`.env`：

```env
DATABASE_URL=postgresql+psycopg://mistakebook:change-me@postgres:5432/mistakebook
POSTGRES_PASSWORD=change-me
PGDATA_DIR=./pgdata
```

> 需要镜像内包含 `psycopg`。若使用官方镜像，请在 `backend/requirements.txt` 追加
> `psycopg[binary]==3.2.3` 后重新构建。

## 启用 AI

**方式一（推荐）：设置页**

登录后进入「设置 → AI 配置」，选择提供方、粘贴 API Key，点「测试连接」验证后再保存。
配置写入数据库并立即生效，无需重启容器。

**方式二：环境变量**（适合批量部署，优先级低于设置页）

```env
AI_PROVIDER=deepseek
AI_API_KEY=sk-xxxx
AI_MODEL=deepseek-chat
```

Ollama（模型跑在宿主机）：

```env
AI_PROVIDER=ollama
AI_BASE_URL=http://host.docker.internal:11434
AI_MODEL=llama3
```

同时取消 `docker-compose.yml` 中 `extra_hosts` 的注释。

> 页面保存的配置会覆盖环境变量。若忘记密钥，可在设置页点「清除已保存的密钥」回到环境变量默认值。

## 文字识别

默认使用 CPU 版 RapidOCR，支持中文印刷体检测与文本提取；首次识别会加载模型，速度取决于 CPU。
手写体、数学公式、模糊或倾斜照片仍可能出错，最终收录始终由用户确认。

若需关闭文字识别、仅使用版面切分与红笔概率评估：

```env
OCR_PROVIDER=heuristic
```

可选替代引擎：

**Tesseract**

```bash
# 宿主或镜像中安装 Tesseract 引擎与中文数据后
pip install -r backend/requirements-ocr.txt
```

```env
OCR_PROVIDER=tesseract
```

**PaddleOCR**（效果更好，镜像显著变大）

```env
OCR_PROVIDER=paddle
```

并在镜像内安装 `paddleocr` / `paddlepaddle`。

> 若提供方初始化失败，系统会自动回退到内置引擎并在 `/api/v1/status` 中说明原因。

## NAS 共享目录 / MinIO 存储

**NAS 目录**：保持 `STORAGE_BACKEND=local`，把 `DATA_DIR` 指向共享目录即可。

**MinIO**：

```env
STORAGE_BACKEND=minio
MINIO_ENDPOINT=192.168.1.10:9000
MINIO_ACCESS_KEY=...
MINIO_SECRET_KEY=...
MINIO_BUCKET=mistakebook
MINIO_SECURE=false
```

需要镜像内安装 `minio` 包。

> 无论使用哪种后端，原始图片都完整保留，不会只保存 OCR 结果。

## 备份与恢复

备份 `DATA_DIR`（SQLite 数据库 + `storage/` 原始图片）：

```bash
docker compose stop mistakebook
tar -czf mistakebook-backup-$(date +%F).tar.gz -C deploy data
docker compose start mistakebook
```

恢复：

```bash
docker compose stop mistakebook
rm -rf deploy/data && tar -xzf mistakebook-backup-YYYY-MM-DD.tar.gz -C deploy
docker compose start mistakebook
```

使用 PostgreSQL 时同时备份 `pgdata` 卷或用 `pg_dump`。

**建议**：每月至少做一次恢复演练，确认备份可用。

## 升级

```bash
git pull
cd deploy
docker compose up -d --build
```

数据库表由 SQLAlchemy 在启动时自动创建缺失的表。**升级前请先备份**。

## 反向代理与 HTTPS

建议在最外层放置 Nginx/Caddy 并启用 TLS：

```nginx
server {
    listen 443 ssl;
    server_name mistakes.example.com;

    ssl_certificate     /etc/ssl/fullchain.pem;
    ssl_certificate_key /etc/ssl/privkey.pem;

    client_max_body_size 40m;   # 必须 >= MAX_FILE_BYTES

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 300s;
    }
}
```

> 开放公网前请阅读 `docs/Security.md` 的加固清单。

### PWA / 摄像头的前提条件

| 能力 | 要求 |
| --- | --- |
| 调起摄像头拍照 | 安全上下文：`https://` 或 `localhost`（浏览器规范） |
| 安装到主屏幕 | 需要 manifest + service worker，同样要求安全上下文 |
| 离线打开外壳 | 同上；首次访问成功后才能缓存 |

也就是说：

- 本机 `http://localhost:8080` —— 全部可用
- 局域网 `http://192.168.1.20:8080` —— 拍照/浏览正常，但**无离线缓存、无法安装**
- 公网 `https://mistakes.example.com` —— 全部可用（推荐，手机使用几乎必须）

如果只在局域网使用又想要 PWA 能力，可以让反向代理签发一张内网自签证书，或者用
`mkcert` 生成被本机信任的证书。自签证书在手机上需要手动把根证书装进系统信任链，
否则浏览器仍按不安全上下文处理。

Service Worker 会缓存应用外壳（HTML/JS/CSS/图标），**不缓存 `/api/` 请求**，
因此在共用设备上切换账号不会看到上一个账号的数据。升级后若发现前端没更新，
可直接刷新一次页面（新的 worker 会 `skipWaiting` 接管）。

## 常见问题

| 现象 | 排查方向 |
| --- | --- |
| 手机上「拍照上传」没反应 | 必须用 https 或 localhost 访问；http 局域网地址会退化成普通文件选择 |
| 设置页没有「一键安装」按钮 | iOS Safari 不支持自动安装，请用 分享 → 添加到主屏幕 |
| 换了账号还能看到旧数据 | 确认访问的是最新构建；SW 只缓存外壳不缓存 `/api/`，若仍异常请强制刷新 |
| 登录提示「用户名或密码不正确」 | 用户名与邮箱都可登录；确认大小写与是否是多账号中的另一个 |
| 成员账号访问 `/admin` 被弹回首页 | 预期行为：监管页仅管理员可见（接口同时返回 403） |
| 容器不断重启 | `docker compose logs mistakebook`；多为 `SECRET_KEY` 未设置或数据目录不可写 |
| 上传后状态 `failed` | 查看试卷的 `error_message`；常见为文件类型不符或 PDF 损坏 |
| 页面图片 404 | 检查 `DATA_DIR` 是否被改动、存储目录是否可读 |
| 工作台空白 | 该页未检测到题目区域；可查看原图确认扫描质量 |
| AI 不生效 | `/api/v1/status` 中 `ai_available` 是否为 `true`；检查密钥与网络 |
| 导出中文文件名乱码 | 已使用 RFC 5987；若仍异常请升级浏览器或反向代理 |
| 内存占用高 | 降低 `MAX_UPLOAD_FILES`，或关闭重型 OCR 提供方 |

## 健康检查

```bash
curl -fsS http://localhost:8080/api/v1/health
```

Dockerfile 已内置 `HEALTHCHECK`，`docker compose ps` 会显示健康状态。

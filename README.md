# MistakeBook AI

面向家庭的 AI 错题本系统。上传试卷照片或 PDF，系统自动切题、检测批改痕迹、计算错题概率并**推荐**错题；**最终是否收录由你决定**。

> 核心原则：**AI 只推荐，用户做决定。** 系统绝不会自动把题目加入错题本。

## 功能

| 模块 | 能力 |
| --- | --- |
| 上传 | JPG / JPEG / PNG / WEBP / PDF，单次 1–500 个文件，PDF 自动分页渲染 |
| OCR | 默认 RapidOCR 中文印刷体识别 + 版面检测；人工作最终确认；可退回仅版面/红笔分析 |
| 自动切题 | 行投影版面分析 → 题目区域坐标（x / y / width / height） |
| 批改识别 | 红笔像素分析 + 叉号/对勾形状判别 → 0–100 错题概率 |
| 错题确认工作台 | **在原图上单击 `+` / `✓` 确认**，无弹窗；右侧错题篮可定位跳转 |
| 批量操作 | 全选 / 全不选 / 仅选 AI 推荐 / 按档位筛选 |
| 错题库 | 搜索、学科/知识点筛选、难度、错误次数排序、标签 |
| AI 分析 | OpenAI / DeepSeek / GLM / Gemini / Claude / Ollama 统一接口，失败自动降级 |
| 复习 | 艾宾浩斯 1 / 3 / 7 / 15 / 30 天，可配置；今日 / 本周 / 本月 |
| 统计 | 学科分布、错误原因、重复错误排行、长期薄弱知识点 |
| 导出 | PDF / DOCX / Markdown / Obsidian |
| 手机 / 平板 | 直接调用摄像头拍照上传（`capture=environment`），相册多选，UI 全响应式 |
| 多账号 | 用户名或邮箱均可登录；每个账号数据完全隔离 |
| 监管 | 管理员可只读查看其他成员的学习数据（试卷 / 错题 / 薄弱点） |
| PWA | 可「添加到主屏幕」，离线可打开外壳，支持从根目录安装 |
| 部署 | Docker Compose，支持群晖 / 威联通 / 绿联 / 极空间 / Linux / Windows Docker Desktop |

## 快速开始（Docker）

```bash
cd deploy
cp .env.example .env
# 修改 SECRET_KEY
docker compose up -d --build
```

打开 <http://localhost:8080>，注册第一个账号（自动成为管理员）。

注册时**用户名必填、邮箱可选**：手机用户可以直接用「妈妈」「小明」这类用户名登录，
不需要先准备邮箱。首个账号是管理员，之后注册的都是普通成员，彼此数据互相隔离。

数据保存在 `deploy/data/`（数据库 + 原始上传文件）。

### NAS 部署

把 `DATA_DIR` 指向共享文件夹子目录，例如群晖：

```env
DATA_DIR=/volume1/docker/mistakebook/data
```

确保容器内用户 `10001` 对该目录有读写权限；原始图片始终保留，不做只存 OCR 结果的裁剪。

### 使用 PostgreSQL

```bash
docker compose --profile postgres up -d
```

并在 `.env` 中设置：

```env
DATABASE_URL=postgresql+psycopg://mistakebook:mistakebook@postgres:5432/mistakebook
```

## 本地开发

后端：

```bash
python -m venv .venv
.venv/Scripts/pip install -r backend/requirements.txt    # Windows
# source .venv/bin/activate && pip install -r backend/requirements.txt
cd backend && uvicorn app.main:app --reload --port 8000
```

前端：

```bash
cd frontend
npm install
npm run dev      # http://localhost:5173，已代理 /api 到 8000
```

API 文档：<http://localhost:8000/docs>

## AI 配置

有两种方式，**推荐在设置页配置**（保存即生效，无需重启）：

1. 登录 → **设置** → **AI 配置**
2. 选择提供方（OpenAI / DeepSeek / GLM / Gemini / Claude / Ollama）
3. 填写 API Key，必要时填写自定义网关或 Ollama 地址
4. 点「测试连接」确认可用，再点「保存配置」

密钥经派生自 `SECRET_KEY` 的密钥加密后存于数据库，接口不会回传明文；页面上仅显示掩码
（如 `••••••••7890`）。仅管理员可修改，普通成员可查看当前状态。

也可用环境变量预置（适合批量部署，优先级低于页面配置）：

```env
AI_PROVIDER=deepseek
AI_API_KEY=sk-...
AI_MODEL=deepseek-chat
```

AI 未配置时：错题浏览、编辑、复习、统计、导出全部可用；AI 分析与出题会提示未配置，或降级为本地规则分析。

文字识别默认由 CPU 版 RapidOCR 提供（中文印刷体）；首次处理可能需要数秒。手写内容、复杂公式和拍摄模糊仍可能识别不准，系统只把结果作为题目文本/区域建议，不会自动收录。若不需要文字识别，可设 `OCR_PROVIDER=heuristic`；`tesseract` / `paddle` 是可选替代引擎。

## 手机 / 平板使用

### 拍照上传

「上传试卷」页提供两个入口：

- **拍照上传** —— 手机/平板直接唤起后置摄像头，拍完自动追加到待上传列表
- **从相册或文件选择** —— 支持多选，图片与 PDF 混合

在桌面浏览器上两个按钮都退化为普通文件选择框，不报错。

系统会自动适配不同尺寸：≤980px 顶部导航收进抽屉菜单；≤640px 表格在容器内横向滚动，
输入框字号提升到 16px（避免 iOS 聚焦时自动缩放），底部按钮高度不低于 40px，
并预留刘海屏安全区（`env(safe-area-inset-*)`）。

### 安装到主屏幕（PWA）

- **Android / Chrome / Edge**：地址栏右侧会出现安装图标，或在设置页点「一键安装」
- **iOS / Safari**：分享 → 添加到主屏幕（iOS 不支持自动安装提示）

安装后以独立窗口运行，无浏览器地址栏；快捷方式包含「拍照上传」（直达
`/upload?camera=1`）与「错题库」。

> Service Worker 只在**安全上下文**中生效：`https://` 或 `localhost`。
> 局域网用 `http://192.168.x.x:8080` 访问时可以正常拍照、浏览，但没有离线缓存，
> 也无法安装到主屏幕——这是浏览器规范限制，不是本项目的 bug。

PWA 是刻意选择的第一步：先在真机上验证产品，再决定是否封装 Android APK / iOS App。

## 账号与权限

| 角色 | 数据范围 | 设置页 | 监管 |
| --- | --- | --- | --- |
| `admin`（首个注册账号） | 自己的数据 | 可修改 AI 配置 | 可查看全部账号（只读） |
| `member` | 仅自己的数据 | 仅查看 | 无权限（接口返回 403，前端也无入口） |

- 登录标识是**用户名或邮箱**，二者等价，错误提示统一为「用户名或密码不正确」
- 注册时的保留字（`admin` / `root` / `system` 等）不可占用，避免冒充
- 管理员可以查看成员的学习数据，但**不能修改或删除**成员的任何内容——
  保持数据归属清晰，避免「监管」变成静默篡改
- 修改密码需要提供当前密码；昵称可随时自助修改

## 目录结构

```
docs/            产品、架构、数据库、UI、测试、安全文档；RFC 与变更记录
database/        schema.sql（SQLite / 与 ORM 保持一致）
backend/app/     FastAPI 应用
  api/           路由：auth / papers / mistakes / reviews / analytics / ai / export
  services/      ocr（区域引擎） / ai / dedup / review / export
  models.py      SQLAlchemy 模型
frontend/src/    Vue3 + TS + NaiveUI
  views/WorkspaceView.vue   错题确认工作台（核心交互）
deploy/          Dockerfile / docker-compose.yml / .env.example
```

## 关键设计决策

1. **预选不等于收录**：`wrong_probability >= 85` 只是默认勾选界面状态；写入数据库的唯一路径是用户点击「加入错题本」。
2. **选择状态按用户隔离**：草稿存在 `question_region_selections`，不与试卷识别产物混在一起，避免家庭成员互相覆盖。
3. **服务端不信任客户端**：坐标、概率、选择状态在提交时由服务端基于数据库重新校验。
4. **单一写入口**：只有 `POST /papers/{id}/commit` 会创建错题记录，便于审计与限流。
5. **监管只读**：管理员能看不能改，避免越权写入破坏数据归属与审计链。
6. **SW 不缓存 API**：共享设备上换账号不会看到上一个账号的残留数据。

## 测试

```bash
# 端到端冒烟：注册 → 上传 → 切题 → 确认 → 收录 → 导出 → 越权检查
.venv/Scripts/python backend/tests/smoke_test.py

# AI 配置：加密存储、掩码、权限、失败降级
.venv/Scripts/python backend/tests/test_ai_settings.py

# 旧库迁移：email-only users 表重建为 username 必填
.venv/Scripts/python backend/tests/test_migration.py

# 账号体系：用户名/邮箱登录、数据隔离、管理员只读监管、PWA 端点
.venv/Scripts/python backend/tests/test_accounts.py
```

前端类型检查与构建：

```bash
cd frontend
npx vue-tsc --noEmit
npm run build
```

## 许可

MIT

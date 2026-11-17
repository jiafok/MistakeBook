# MistakeBook AI API 文档

基础路径：`/api/v1`。除 `/status`、`/health`、`/auth/register`、`/auth/login` 外，全部需要
`Authorization: Bearer <token>`。

交互式文档：`/docs`（Swagger UI）、`/redoc`。

## 认证

账号标识是 **用户名**（必填、唯一，3–64 位字母/数字/下划线/连字符，保留字如 `admin` 不可用）；
**邮箱可选**，用于找回或后续通知。注册时二选一即可，手机用户可以只用用户名，不必准备邮箱。

### POST /auth/register

```json
{ "username": "mama", "password": "secret123", "display_name": "妈妈" }
```

```json
{ "email": "parent@example.com", "password": "secret123" }
```

- 只传 `email` 时，系统取邮箱 `@` 前缀作为用户名（冲突时自动加 `_2`、`_3` 后缀）
- `201` → `{ access_token, token_type, user }`
- `409` 用户名或邮箱已存在；`403` 实例关闭注册；`422` 校验失败

首个注册账号自动获得 `admin` 角色。

### POST /auth/login

```json
{ "identifier": "mama", "password": "secret123" }
```

`identifier` 可填用户名或邮箱，二者等价；`email` / `username` 也作为兼容别名被接受。

- `200` → `{ access_token, token_type, user }`
- `401` 凭据错误（统一提示「用户名或密码不正确」，不暴露账号是否存在）

### GET /auth/me

返回当前用户。`401` 未登录/令牌失效。

### PATCH /auth/me

修改自己的昵称或密码。用户名与角色不可自助修改。

```json
{ "display_name": "妈妈" }
```

```json
{ "current_password": "secret123", "new_password": "newpass456" }
```

- `200` → 更新后的 user
- `400` 当前密码不正确，或未提供 `current_password`
- 修改密码后旧的 JWT 仍在有效期内；如需强制下线，请在服务端轮换 `SECRET_KEY`。

## 试卷与确认工作台

### POST /papers/upload

`multipart/form-data`：`files`（可重复）、`title`、`subject`。

- `201` → `Paper`。`status` 为 `ready`（有页面成功处理）或 `failed`（全部失败）
- 部分文件失败时 `status=ready` 且 `error_message` 记录失败原因
- `400` 超过 500 个文件、类型不支持、内容与扩展名不符、PDF 页数超限

### GET /papers

返回当前用户的试卷列表（最多 200 条，按创建时间倒序）。

### GET /papers/{paper_id}

`404` 当试卷不存在或不属于当前用户（不泄露他人资源存在性）。

### DELETE /papers/{paper_id}

删除试卷及其页面、区域；同时尝试清理存储文件。`204` 无内容。

### GET /papers/{paper_id}/pages/{page_no}/image

返回页面 PNG。

> **注意**：`<img>` 标签无法发送 `Authorization` 头，因此该端点同时接受
> `?token=<jwt>` 查询参数。前端 `pageImageUrl()` 已自动附加。

### POST /papers/{paper_id}/reanalyze?detect_subject=true

重新切题；`detect_subject=true` 时同时按识别文字重新判断学科。
`subject_source` 会标为 `auto`。不带该参数时，用户手动指定的学科不会被覆盖。

## 自动判断学科

上传时 `subject` 参数视为**提示**：

| 传入值 | 行为 |
| --- | --- |
| 省略 / `auto` / `未分类` | 服务端根据识别到的文字判断学科，`subject_source=auto` |
| 任何具体学科（如 `化学`） | 尊重用户选择，`subject_source=user`，识别结果不会覆盖 |

判断逻辑是离线关键词打分（`backend/app/services/subject.py`）：每个学科有强/弱关键词，
按识别全文加权计分，**只有明显领先且证据充分时才给出结论**，否则返回空并保留用户选择。
覆盖 9 个学科：数学 / 语文 / 英语 / 物理 / 化学 / 生物 / 历史 / 地理 / 政治。

`PaperOut.subject_source` 取值：`auto`（自动识别）或 `user`（手动指定）。

### POST /papers/{paper_id}/reanalyze

用当前 OCR 引擎重新切题，返回更新后的 paper。

区域在**上传时**就已写入数据库，换了识别引擎后旧试卷会保留旧框。该端点用已保存的页面
图片重新计算全部区域：

- 删除该试卷所有旧区域及关联的用户勾选（区域 id 会重新生成）
- 按新结果重建 `question_regions`
- 回写 `page.width/height/ocr_text` 与 `paper.ocr_provider`

`200` → paper；`409` 页面图片不可用（存储丢失）。

### GET /papers/{paper_id}/workspace

工作台数据源（`regions[].x/y/width/height` 是**原图像素坐标**，前端据此在原图上叠加可点选方框）：

```json
{
  "paper": { "id": 1, "title": "...", "subject": "数学", "status": "ready" },
  "pages": [{ "id": 1, "page_no": 1, "width": 1240, "height": 1754, "image_url": "..." }],
  "regions": [
    {
      "id": 1, "page_no": 1, "page_id": 1,
      "x": 76, "y": 465, "width": 1023, "height": 132,
      "region_order": 1, "label": "第 2 题", "text": "",
      "wrong_probability": 100, "band": "recommended",
      "detection_status": "ok",
      "is_selected": true, "is_recorded": false
    }
  ],
  "counts": { "selected": 1, "new": 1, "existing": 0, "total": 4 }
}
```

`is_selected` 为**当前用户的草稿选择**；`>=85` 的区域在未操作前默认为 `true`。
`is_recorded` 表示已在错题本中，此时 `is_selected` 恒为 `true` 且不可取消。

### PATCH /papers/{paper_id}/selections

保存草稿，按用户隔离，不影响他人：

```json
{ "selections": { "12": true, "13": false } }
```

不属于该试卷的区域 id 会被静默跳过。

### POST /papers/{paper_id}/commit

**唯一**创建错题记录的入口。

```json
{ "region_ids": [12, 13] }
```

响应：

```json
{
  "created": 1, "existing": 1, "failed": 0,
  "items": [
    { "region_id": 12, "status": "created", "mistake_id": 5, "reason": "" },
    { "region_id": 13, "status": "existing", "mistake_id": 3, "reason": "已存在" }
  ]
}
```

服务端在此重新校验：

1. 区域属于该试卷且试卷属于当前用户
2. 坐标在页面范围内（不信任客户端）
3. 是否已存在（同一用户 + 同一题目唯一）

重复提交不会产生重复记录，`wrong_count` 会累加。

## 错题库

### GET /mistakes

查询参数：`q`、`subject`、`knowledge_point`、`tag`、`difficulty`、`status`（默认 `active`）、
`sort`（`recent` / `oldest` / `wrong_count`）、`page`、`page_size`（≤100）。

### GET /mistakes/{id} · PATCH · DELETE

`PATCH` 可更新 `subject`、`difficulty`、`note`、`status`、`tags`、`knowledge_points`、
`error_reason`、`correct_solution`。

### POST /mistakes/{id}/review-plan

为错题创建复习计划（已存在 pending 计划则复用）。`201`。

## 复习

### GET /reviews?horizon=today|week|month

`today` 只返回已到期项；`week`/`month` 返回该时间窗内到期项。

### POST /reviews/{plan_id}/result

```json
{ "result": "pass" }
```

`pass` 进入下一间隔；`fail` 重置到第一间隔并保留历史；`skip` 顺延一天。

### GET /reviews/history?limit=50

## 统计

### GET /analytics

```json
{
  "total_mistakes": 12,
  "new_last_7_days": 3,
  "by_subject": { "数学": 8, "英语": 4 },
  "top_knowledge_points": [{ "name": "一元二次方程", "count": 5 }],
  "top_wrong_count": [{ "mistake_id": 3, "label": "第 2 题", "wrong_count": 4 }],
  "error_reasons": { "计算错误": 6 },
  "review_completion_rate": 0.5,
  "repeat_wrong": [],
  "weak_knowledge_points": []
}
```

### GET /knowledge-points · GET /tags

## AI

### POST /ai/analyze

```json
{ "question_id": 5, "apply": true }
```

`apply=true` 时把结果写入 `ai_analysis` 并更新题目学科/难度/知识点。

未配置提供方或调用失败时返回 `provider=local`、`is_fallback=true` 并给出 `error` 说明，
**不会返回 5xx**，不影响已保存数据。

### POST /ai/practice

```json
{ "mistake_id": 3, "count": 5, "save": true }
```

`count` 必须是 5 / 10 / 20。未配置 AI 时 `questions` 为空并返回 `error`。

> 隐私提示：配置外部提供方后，题目文本会发送到该服务。请在家庭内部达成知情同意。

## 导出

### GET /export/{fmt}

`fmt` ∈ `pdf` | `docx` | `markdown` | `obsidian`。可选 `paper_id`、`subject`。

中文文件名按 RFC 5987 编码，避免 HTTP 头 latin-1 限制。


## 设置（AI 配置）

AI 提供方可在**设置页直接配置**，保存后立即生效，无需重启容器。配置存储于数据库，
优先级高于环境变量；环境变量作为初始默认值。

### GET /settings/ai

任何已登录用户可读。返回**不含明文密钥**的安全表示：

```json
{
  "provider": "deepseek",
  "model": "deepseek-chat",
  "base_url": "",
  "timeout_seconds": 60,
  "has_api_key": true,
  "api_key_source": "db",
  "api_key_masked": "••••••••7890",
  "configured": true,
  "providers": [{ "value": "openai", "label": "OpenAI", "needs_key": "true", "needs_base": "false" }],
  "default_models": { "deepseek": "deepseek-chat" },
  "env_fallback": { "provider": "none", "has_api_key": false, "base_url": false, "model": "" }
}
```

### PUT /settings/ai

仅管理员（`role=admin`）。`api_key` 语义：

| 传入值 | 效果 |
| --- | --- |
| 省略 / `null` | 保持不变 |
| `""` | 清除已保存的密钥 |
| 实际密钥 | 加密后写入 |
| `••••••••xxxx`（掩码占位） | 忽略，保持不变 |

```json
{ "provider": "deepseek", "api_key": "sk-...", "model": "deepseek-chat", "timeout_seconds": 60 }
```

- `400` 提供方非法、`base_url` 非 http(s)、Ollama 缺少地址
- `403` 非管理员

密钥使用由 `SECRET_KEY` 派生的密钥经 Fernet 加密后存入 `system_settings`，
接口永不回传明文；直接读取数据库文件也看不到原始密钥。

### POST /settings/ai/test

仅管理员。用表单值或已保存值发起一次最小真实请求，用于验证连通性。
不会返回 5xx：失败时返回 `ok=false` 与可读的 `message`（含上游状态码）。

```json
{ "provider": "deepseek", "api_key": "sk-..." }
```

```json
{ "ok": false, "provider": "deepseek", "model": "deepseek-chat",
  "latency_ms": 812, "message": "HTTP 401：Authentication Fails..." }
```

> 安全提示：未配置或调用失败时，系统降级为本地规则分析，错题数据不受影响。

## 管理员监管（只读）

只有 `role=admin` 的账号可访问，其他账号一律 `403`。
监管是**只读**的：管理员可以查看成员的学习数据，但无法增删改成员的试卷与错题，
所有写操作仍归属于数据所有者本人。

### GET /admin/users

返回全部账号及其汇总计数：

```json
[
  {
    "id": 2, "username": "jeffrey", "email": "jeffrey@example.com",
    "display_name": "小明", "role": "member", "is_active": true,
    "created_at": "2026-11-17T03:01:47", "paper_count": 1,
    "mistake_count": 12, "pending_review_count": 3, "is_self": false
  }
]
```

### GET /admin/users/{user_id}/summary

`{ user, paper_count, mistake_count, archived_count, subject_distribution,
review_plan_count, review_done_count, review_completion_rate }`

### GET /admin/users/{user_id}/papers

该成员的试卷列表（最多 200 条，按上传时间倒序）。

### GET /admin/users/{user_id}/mistakes?q=&subject=&page=&page_size=

该成员的有效错题分页，结构同 `GET /mistakes`。

### GET /admin/users/{user_id}/weak-points

该成员错误次数最高的 10 个知识点：`[{ "name": "一元二次方程", "mistake_count": 5 }]`

## PWA（根路径，不在 `/api/v1` 下）

Service Worker 必须位于站点根目录才能控制整个 scope，因此这三个端点没有 api 前缀。

### GET /manifest.webmanifest

Web App Manifest：`display=standalone`、192/512 图标、maskable 图标、
快捷方式「拍照上传」（`/upload?camera=1`）与「错题库」（`/mistakes`）。

### GET /sw.js

缓存策略：

- 导航请求：网络优先，离线时回退到缓存的 `index.html`
- 静态资源（带指纹的 js/css/图片）：缓存优先
- **`/api/` 请求永不缓存** —— 共享设备上不会把上一个账号的数据留给下一个账号

响应头包含 `Cache-Control: no-cache` 与 `Service-Worker-Allowed: /`。

### GET /offline

离线兜底页面（纯 HTML，无外部依赖）。

## 系统

### GET /status

```json
{
  "app": "MistakeBook AI",
  "version": "1.1.0",
  "storage_backend": "local",
  "ai_provider": "none",
  "ai_available": false,
  "ocr": {
    "engine": "question-region-engine",
    "ocr_provider": "heuristic",
    "ocr_available": true,
    "ocr_reason": "内置启发式引擎：仅做版面切分与红笔分析，不含文字识别",
    "text_extraction": false
  },
  "limits": { "max_upload_files": 500, "max_file_mb": 30.0, "max_pdf_pages": 100 },
  "thresholds": { "recommend": 85, "suspect": 50 },
  "review_intervals": [1, 3, 7, 15, 30]
}
```

### GET /health

供容器健康检查使用，返回 `{ "status": "ok" }`。

## 错误格式

统一为 `{ "detail": "..." }`。校验错误时 `detail` 为对象数组。

| 状态码 | 含义 |
| --- | --- |
| `400` | 请求不合法（数量、格式、坐标、参数） |
| `401` | 未登录或令牌失效 |
| `403` | 无权限或功能被关闭 |
| `404` | 资源不存在或不属于当前用户 |
| `409` | 冲突（如邮箱已注册） |
| `500` | 未预期错误（日志记录详细信息，响应不泄漏内部信息） |

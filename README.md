# Moyo Memory

一个基于 LangGraph 的结构化用户长期记忆服务。它从用户消息中提取明确表达的个人资料、偏好和重大事件，经策略过滤与确定性冲突处理后保存到 MySQL，并使用 Qdrant 提供可降级的语义向量召回。

## 设计重点

- LangGraph 只编排处理流程，MySQL 是长期记忆的事实来源。
- 每条记忆保留证据、来源消息、置信度、有效期和状态。
- 模型只能提出候选事实，数据库变更由程序规则执行。
- 展示值和规范值保持用户原文语言；检测到模型擅自翻译时会拒绝结果并重试。
- 支持 `replace`、`append`、`retract`，能够表达更正、累积偏好和撤回。
- 密码、令牌、银行卡完整号码等 `restricted` 信息在写库前被拦截。
- 普通敏感信息先进入待确认区，确认前不会被召回或写入 Qdrant。
- 用户可以关闭长期记忆、限制记忆类型或屏蔽具体字段。
- 支持单条确认、拒绝、纠正、删除，纠正前版本保留为历史记录。
- 记忆管理操作写入不含正文的审计日志，并提供状态指标。
- 重复请求和重复事实不会生成重复的活跃记忆。
- MySQL 是唯一事实来源，Qdrant 是可重建的派生索引。
- 事务型 outbox 保证数据库提交后不会丢失向量索引任务。
- 向量服务故障时自动降级为词法与知识图谱召回。

## 目录

```text
src/moyo_memory/
  api.py          FastAPI 接口
  config.py       环境配置
  db.py           MySQL 表与仓储逻辑
  extractor.py    模型结构化提取
  graph.py        LangGraph 工作流
  retrieval.py    混合召回与可解释评分
  reranking.py    可选的专用文本模型二次精排
  evaluation.py   离线召回评测指标与命令
  semantic.py     Embedding 与 Qdrant 适配器
  index_worker.py 可靠向量索引 worker
  chat.py         多轮对话、上下文窗口与滚动摘要
  models.py       输入输出领域模型
tests/            不调用真实模型的流程测试
```

## 启动

要求 Python 3.11+ 和 Docker。

```bash
docker compose up -d mysql qdrant
python -m venv .venv
# Windows PowerShell
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
Copy-Item .env.example .env
```

编辑 `.env`，填写新生成的 `MODEL_API_KEY`。项目默认使用千问兼容接口：

```dotenv
MODEL_API_KEY=在本地填写新密钥
MODEL_NAME=qwen3.8-max
MODEL_BASE_URL=https://maas.qianwenaiapi.com/compatible-mode/v1
MODEL_ENABLE_THINKING=true

SEMANTIC_RECALL_ENABLED=true
EMBEDDING_MODEL=text-embedding-v4
EMBEDDING_BASE_URL=https://maas.qianwenaiapi.com/compatible-mode/v1
EMBEDDING_DIMENSIONS=1024
QDRANT_URL=http://127.0.0.1:6333

RERANK_ENABLED=false
RERANK_MODEL=qwen3.7-text-rerank
RERANK_ENDPOINT=https://maas.qianwenaiapi.com/api/v1/services/rerank/text-rerank/text-rerank
RERANK_API_KEY=
RERANK_MIN_SCORE=0.20
RERANK_MAX_CANDIDATES=24
RERANK_TIMEOUT_SECONDS=20

# 生产环境开启。用户请求携带 X-User-ID，管理员请求携带 X-Admin-Key。
AUTH_ENABLED=false
AUTH_ADMIN_KEY=请生成高强度随机值

# 一轮是一组 user/assistant 消息；轮数和 token 预算同时生效。
CHAT_HISTORY_MAX_TURNS=12
CHAT_CONTEXT_MAX_TOKENS=16000
CHAT_MAX_OUTPUT_TOKENS=2048
CHAT_TEMPERATURE=0.3
CHAT_SUMMARY_ENABLED=true
```

`.env` 已被 Git 忽略，不要把真实密钥写入 `.env.example` 或提交到版本库。后台记忆提取使用完整的结构化响应，不启用流式输出；这可以确保模型结果先通过 Pydantic 校验，再执行数据库操作。

`EMBEDDING_API_KEY` 留空时复用 `MODEL_API_KEY`。启用语义检索前必须确认模型服务支持 OpenAI 兼容的 `/embeddings` 接口，且返回维度与 `EMBEDDING_DIMENSIONS` 一致。

```bash
uvicorn moyo_memory.api:app --reload
```

另开一个进程运行向量索引 worker。首次启用或修改 embedding 模型时执行全量回填：

```bash
moyo-index-worker --backfill
```

后续正常启动：

```bash
moyo-index-worker
```

打开 `http://127.0.0.1:8000/docs` 调试接口。

直接使用浏览器聊天界面：`http://127.0.0.1:8000/chat/{user_id}`，例如
`http://127.0.0.1:8000/chat/user-001`。页面支持会话列表、新建与删除会话、连续聊天、
长期记忆查看、本轮召回查看和知识图谱跳转。知识图谱页面每 3 秒自动刷新当前用户的
最新长期记忆，聊天页底部会显示向量索引的同步状态。聊天页侧栏和图谱页顶部都可以
从数据库用户列表切换测试用户。用户列表接口只返回用户 ID、记忆数、会话数和最后活动
时间，不返回记忆正文。默认关闭鉴权以便本地调试；公网部署必须启用 `AUTH_ENABLED`，
并由可信的登录网关注入 `X-User-ID`，不能直接信任浏览器自行声明的用户 ID。

聊天消息的长期记忆提取默认最多尝试 3 次（`MEMORY_EXTRACTION_MAX_ATTEMPTS`）。每条
消息的提取结果会记录在 `memory_extractions` 表中；客户端使用相同 `message_id` 重试时，
只会补偿失败的提取，不会再次生成对话回复或重复提取已经成功的消息。

如果使用本机已安装的 MySQL 而不是 Docker，请先以管理员身份执行
[`scripts/init_mysql.sql`](scripts/init_mysql.sql)，或在 `.env` 中把 `DATABASE_URL`
改成已有数据库账号。示例密码只用于本地开发，部署时必须更换。

## 示例

写入一条消息：

```bash
curl -X POST http://127.0.0.1:8000/v1/memories/extract \
  -H "Content-Type: application/json" \
  -d '{"user_id":"user-001","message_id":"msg-001","content":"我叫陈晨，喜欢徒步，去年搬到了杭州"}'
```

查询当前画像：

```bash
curl http://127.0.0.1:8000/v1/users/user-001/profile
```

删除用户的全部会话、消息和长期记忆：

```bash
curl -X DELETE http://127.0.0.1:8000/v1/users/user-001/memories
```

## 多轮对话

先创建会话：

```bash
curl -X POST http://127.0.0.1:8000/v1/users/user-001/conversations \
  -H "Content-Type: application/json" \
  -d '{"title":"日常助手"}'
```

使用返回的 `id` 发送消息；`message_id` 是客户端生成的幂等键，重试时必须保持不变：

```bash
curl -X POST http://127.0.0.1:8000/v1/users/user-001/conversations/会话ID/messages \
  -H "Content-Type: application/json" \
  -d '{"message_id":"msg-001","content":"我叫陈晨，平时喜欢徒步"}'
```

该接口会自动召回长期记忆、加载最近对话与滚动摘要、生成回答、保存双方消息，最后从本次用户消息中提取长期记忆。默认携带最近 12 轮且总上下文不超过 16000 个估算 token；超出部分压缩进会话摘要，而不是直接丢弃。所有会话和消息查询都同时使用 `user_id` 与 `conversation_id` 过滤。

查看最近消息：

```bash
curl "http://127.0.0.1:8000/v1/users/user-001/conversations/会话ID?limit=100"
```

## API

- `POST /v1/memories/extract`：提取并保存一条消息中的记忆。
- `GET /v1/users?q=&limit=100&offset=0`：分页查询数据库中的用户。
- `GET /v1/users/{user_id}/profile`：获取当前有效画像和事件。
- `GET /v1/users/{user_id}/memory-items`：获取可管理记忆及待确认状态。
- `GET/PUT /v1/users/{user_id}/memory-policy`：查询或更新用户记忆策略。
- `POST /v1/users/{user_id}/memories/{fact_id}/review`：确认或拒绝敏感记忆。
- `PATCH /v1/users/{user_id}/memories/{fact_id}`：纠正记忆并保留旧版本。
- `DELETE /v1/users/{user_id}/memories/{fact_id}`：撤回单条记忆。
- `GET /v1/users/{user_id}/memory-audit`：查看不含记忆正文的操作审计。
- `GET /v1/users/{user_id}/memory-stats`：查看该用户的记忆状态统计。
- `GET /v1/users/{user_id}/memory-index-status`：查看该用户自己的向量同步状态。
- `GET /v1/users/{user_id}/knowledge-graph`：获取知识图谱节点和边。
- `POST /v1/users/{user_id}/recall`：按问题召回相关事实并沿关系扩展。
- `POST /v1/users/{user_id}/conversations`：创建一个隔离的对话会话。
- `GET /v1/users/{user_id}/conversations`：列出用户自己的会话。
- `GET /v1/users/{user_id}/conversations/{conversation_id}`：分页读取会话历史。
- `POST /v1/users/{user_id}/conversations/{conversation_id}/messages`：执行完整的记忆增强对话。
- `DELETE /v1/users/{user_id}/conversations/{conversation_id}`：删除单个会话及其消息。
- `GET /graph/{user_id}`：打开可拖动、缩放的知识图谱预览。
- `GET /v1/users/{user_id}/memories?include_history=true`：查看记忆历史。
- `DELETE /v1/users/{user_id}/memories`：物理删除该用户的会话、消息和长期记忆。
- `GET /health`：服务与数据库健康检查。
- `GET /health/vector-index`：查看向量索引开关和 outbox 积压。
- `GET /health/metrics`：管理员查看全局记忆与索引状态。

启用鉴权后，用户接口需携带 `X-User-ID`。`GET /v1/users`、
`GET /health/vector-index` 和 `GET /health/metrics` 属于管理员接口，需要
`X-Admin-Key`。本地 `AUTH_ENABLED=false` 时保持原有调用方式不变。

## 知识召回

所有检索先用路径中的 `user_id` 做租户隔离，只会读取该用户当前有效的事实与关系：

```bash
curl -X POST http://127.0.0.1:8000/v1/users/demo-graph/recall \
  -H "Content-Type: application/json" \
  -d '{"query":"他在哪里工作，做什么职位？","limit":5,"include_graph_neighbors":true}'
```

返回的 `hits` 包含事实、命中原因、一跳关系和评分明细：

```json
{
  "score": 0.8321,
  "scores": {
    "lexical": 0.74,
    "semantic": 0.91,
    "confidence": 0.96,
    "recency": 0.99,
    "graph": 0.0,
    "total": 0.8321
  }
}
```

启用 Qdrant 时，直接命中按词法相关度 35%、向量相似度 50%、事实置信度 10%、时间新鲜度 5% 融合，再叠加有衰减的一跳图关系分。向量后端不可用时自动切换为词法相关度 75%、置信度 20%、新鲜度 5%。关键词原始分除以 `max(6, 本次最高关键词分)`，避免少量重合被归一化成满分。

系统会根据“爱好、姓名、经历”等问题词自动限制记忆类型，没有明确意图时查询全部类型。明确的资料问题进一步按字段缩小范围：工作问题保留职业、公司、城市，宠物名字问题不会命中用户姓名。显式 `categories` 优先于自动判断；`auto_category_filter=false` 同时关闭自动类型和字段限制；“最近、去年”等时间词本身不会单独触发事件过滤。

最终门槛为 `max(RECALL_MIN_SCORE, 最高直接匹配分 × RECALL_RELATIVE_SCORE_THRESHOLD)`，默认分别为 `0.35` 和 `0.55`。仅达到门槛的直接匹配结果能向邻居传播关系分，传播使用加分前的分数并固定为一跳。最终结果和关联详情都排除未通过门槛或未入选的事实，未命中时返回空 `hits` 和空 `context`，不会为凑满 `limit` 返回弱相关信息。分数相同时按事实 ID 稳定排序。

调用方可以用 `categories`、`auto_category_filter`、`min_score` 和 `relative_score_threshold` 覆盖默认策略，例如：

```json
{
  "query": "我都有什么爱好",
  "limit": 8,
  "auto_category_filter": true,
  "min_score": 0.35,
  "relative_score_threshold": 0.55
}
```

响应中的 `retrieval_mode`、`applied_categories`、`applied_keys`、`score_threshold` 和 `warnings` 会说明实际使用的召回路径。默认阈值属于初始经验值，需要在真实业务问题上评测调优。`RERANK_ENABLED=true` 时，系统最多取 24 条初排候选，交给专用的 `qwen3.7-text-rerank` 按问题相关性重新排序，再返回调用方要求的条数。精排分数低于 `RERANK_MIN_SCORE`（默认 `0.20`）的候选会被丢弃，因此结果可能少于 `limit`。精排超时、服务异常或响应非法时会自动退回原融合排序，并在 `warnings` 中返回 `reranker_unavailable`。`RERANK_API_KEY` 留空时复用 `MODEL_API_KEY`。自动类型和字段判断采用规则，不是生成模型。

`context` 是可直接拼接进聊天模型系统提示词的精简文本。默认召回只调用 embedding 服务；
仅在显式开启 `RERANK_ENABLED` 后，且初排存在候选时，候选精排阶段才会调用一次专用文本排序模型。

## 记忆生命周期

普通记忆通过策略过滤后直接成为有效事实。`sensitive` 记忆默认进入 `pending`，此时只在
记忆管理页可见，不参与画像、图谱、对话召回和向量索引；用户确认后才生效，拒绝后变为
撤回状态。`restricted` 信息始终拒绝保存。带 `valid_to` 的事实到期后自动退出当前召回。

同一字段中时间更早的事实保存为历史，不覆盖较新的事实。人工纠正会创建新版本并把原事实
标记为 `superseded`；单条删除采用可审计的逻辑撤回。聊天页的记忆抽屉提供这些操作。

## 离线评测

复制 `evals/recall_cases.example.jsonl`，把事实 ID 替换为测试库中的真实 ID，然后运行：

```bash
moyo-evaluate-recall evals/recall_cases.jsonl --base-url http://127.0.0.1:8000 -k 8
```

报告包含 `Recall@K`、`Precision@K`、`MRR` 和无答案问题误召率。修改召回阈值、
embedding 模型或精排模型后，应使用同一份数据集做回归比较。

## 向量索引一致性

事实新增、更新、撤回和删除时，会在同一个 MySQL 事务中更新 `vector_index_outbox`。worker 批量消费事件并执行 Qdrant upsert/delete，失败使用指数退避重试；进程异常退出后会回收超时的 `processing` 任务。

Qdrant payload 同时保存 `user_id`，每次向量查询都强制使用 `user_id` 过滤。业务部署仍须在 API 网关或应用层从登录凭证解析用户身份，不能直接信任客户端提交的任意 `user_id`。

## 生产环境建议

修改示例密码，不要将 MySQL 和 Qdrant 端口暴露到公网，通过迁移工具管理表结构，并为 Qdrant 配置 API Key 或私有网络。生产环境必须启用鉴权，并在反向代理层根据登录会话注入身份头。高敏感业务还应增加字段级加密、密钥轮换和审计日志外部归档。embedding 模型或维度变化时使用新 collection 名称并重新回填，不要向已有 collection 写入不同维度的向量。


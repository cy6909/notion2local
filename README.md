# Notion2Local

Notion2Local 是一个以 Notion 为唯一远端来源的、只读、可恢复本地镜像。它保留原始 API 快照、对象关系、附件和同步状态，再提供本地阅读 API；第三方笔记软件只能作为可选投影，不能替代原始归档。

当前实现基线：

- Python 3.12 + FastAPI + SQLAlchemy + PostgreSQL。
- Notion API 默认版本 `2026-03-11`。
- Webhook 只作为重新抓取信号；事件入箱、幂等、重试和定时对账由同步层负责。
- raw 快照和附件使用宿主机持久化卷；原始内容不会被 Markdown 投影覆盖。
- 本机只编辑代码。构建、测试、Compose 启动、部署和运行验证只在 `10.89.2.39` 完成。

## 本地开发约束

不要在仓库中保存真实 `.env`、Notion token、OAuth secret、管理员密码或 Tunnel token。`.env.example` 只描述变量名和安全占位值。

## 远程启动

在 `10.89.2.39` 的部署目录执行：

```bash
cp .env.example .env
# 编辑 .env，至少设置 DATABASE_URL、POSTGRES_PASSWORD 和 Notion 授权方式
docker compose up -d db
docker compose up -d web worker scheduler
docker compose ps
```

核心入口：

- `/`：当前只读状态页。
- `/healthz`：进程健康检查。
- `/readyz`：数据库就绪检查。
- `/docs`：API 文档。
- `/api/v1/setup/status`：不泄露 secret 的配置状态。

可选阅读器投影：

```bash
docker compose --profile viewer pull viewer
docker compose --profile viewer up -d viewer
```

生产升级前应把 `TRILIUM_IMAGE` 改为经审核的固定版本或 digest；`latest` 只用于首次验证，不作为无人值守升级策略。

## 当前范围

第一阶段先交付 U1～U4 的可验证基础：配置／健康检查、授权边界、Notion 分页与块递归、原始快照、对象目录、关系边和附件接口。U5～U9 会在远程测试根页面、Compose 回执和真实浏览器验证后继续收敛。

详细产品契约、研究、决策和验收门槛以 Notion 中的 `PLAN-NOTION2LOCAL-001`、`ARCH-NOTION2LOCAL-001`、`DEC-NOTION2LOCAL-001`、`FEAT-NOTION2LOCAL-001` 和 `ENG-NOTION2LOCAL-001` 为准。

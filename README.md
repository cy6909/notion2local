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

## 在 10.89.2.39 注入 Notion Token

当前阶段使用 Notion Internal Connection Token。先在 Notion 创建一个只读连接，并把需要归档的根页面（以及其中的数据库）通过页面右上角菜单的 Connections/连接共享给该连接。Notion 连接默认只对显式共享的页面可见，官方步骤见 [Create integrations with the Notion API](https://www.notion.com/en-gb/help/create-integrations-with-the-notion-api)。

然后在 39 机器的 SSH 终端执行以下命令。Token 只在交互式输入和短暂的子进程环境中出现，不要把它粘贴到聊天、Git、Notion、URL 或命令参数中：

```bash
cd /opt/notion2local
read -rsp 'Notion token: ' token; printf '\n'
NOTION_TOKEN="$token" python3 - <<'PY'
from pathlib import Path
import os

path = Path('.env')
token = os.environ['NOTION_TOKEN'].strip()
lines = path.read_text(encoding='utf-8').splitlines()
for index, line in enumerate(lines):
    if line.startswith('NOTION_TOKEN='):
        lines[index] = f'NOTION_TOKEN={token}'
        break
else:
    lines.append(f'NOTION_TOKEN={token}')
path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
PY
unset token NOTION_TOKEN
chmod 600 .env
docker compose up -d --force-recreate web worker scheduler
curl -fsS http://127.0.0.1:8080/api/v1/setup/status
```

最后一条命令不显示 Token，只应看到 `notion_configured=true` 和 `state=needs_root_scope`。不要执行 `docker compose config` 或打印 `.env`，因为这些操作会把 secret 输出到终端。

可选阅读器投影：

```bash
docker compose --profile viewer pull viewer
docker compose --profile viewer up -d viewer
```

生产升级前应把 `TRILIUM_IMAGE` 改为经审核的固定版本或 digest；`latest` 只用于首次验证，不作为无人值守升级策略。

## 当前范围

第一阶段先交付 U1～U4 的可验证基础：配置／健康检查、授权边界、Notion 分页与块递归、原始快照、对象目录、关系边和附件接口。U5～U9 会在远程测试根页面、Compose 回执和真实浏览器验证后继续收敛。

详细产品契约、研究、决策和验收门槛以 Notion 中的 `PLAN-NOTION2LOCAL-001`、`ARCH-NOTION2LOCAL-001`、`DEC-NOTION2LOCAL-001`、`FEAT-NOTION2LOCAL-001` 和 `ENG-NOTION2LOCAL-001` 为准。

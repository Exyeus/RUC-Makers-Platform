# 单台 ECS + 私有对象存储（Issue #85）

本方案从本校仓库构建，不自动更新镜像。当前是实现交付；尚未完成完整镜像启动、MinIO、云端验收或备份恢复演练，不能标记 Cloud-ready / Production-ready。开发环境仍默认 local。不要把本文件的命令直接用于正在运行的实验室活动。

## 文件与限制

| 数据 | 位置 / 备份 |
| --- | --- |
| 数据库、上传凭证、审核记录 | PostgreSQL volume；pg_dump |
| 最终私有作品 | local: `/data/hackathon-private`；s3: 独立私有 bucket/prefix |
| 临时分片 | `/data/hackathon-private/.uploads`；始终是 ECS 本地持久卷 |
| 普通媒体 | `/data/media`，由 HTTPS 代理公开；不迁入作品 bucket |
| 导出、日志、模板、profile、运行材料 | `/data`，备份整个卷；日志含敏感信息须限制访问 |
| 编译前端、静态资源 | 镜像自带；init 将 static.dist 复制到 static 卷 |
| Redis | 独立 AOF 卷；缓存、Channels、Celery 队列；不是业务数据库备份的替代品 |
| TLS 证书 | caddy-data / caddy-config 卷，纳入主机备份 |

单机优先。Web/WebSocket/worker 必须共享同一 data 卷；上传配额和租约仍沿用数据库锁。S3 只替换最终文件，不能据此声称多机无状态扩展。文件大小和并发槽位沿用业务限制。S3 上传仅持有会话锁，提交前重新校验活动状态、权限、截止；大文件仍消耗工作进程。只运行一个 Beat。不要在比赛中开启自动镜像更新。

## 首次部署

要求 Linux ECS、Docker Engine 与 Compose v2、域名解析到 ECS、80/443 入站。不要开放 PostgreSQL、Redis 或应用端口到公网。仓库中不保存真实凭据。

```bash
cd deployment
cp .env.example .env
chmod 600 .env
# 填写域名、随机 secret/password、SMTP；镜像标签与 REVISION 使用实际完整 commit SHA。
docker compose config --quiet
docker compose build web
# 后续服务复用同一 APP_IMAGE，不再分别 build。
docker compose up -d db redis
docker compose --profile ops run --rm init
docker compose --profile ops run --rm migrate
docker compose up -d --no-build web websocket worker beat proxy
docker compose ps
docker compose logs --tail=100 web websocket worker beat proxy
```

init 和 migrate 为显式步骤；启动 Web、worker、Beat 不执行 migrate。升级时先备份并停止所有写入者，记录镜像 SHA，更新镜像，执行 init 和一次 migrate，再启动服务。不要使用 `down -v`。init 刷新静态卷前清除旧文件需在维护窗口由运维确认；当前复制保留旧静态文件，可在回退中继续使用。新增主机配置时检查 DNS、HTTPS、SMTP 和活动默认入口；数据库中的活动发布配置仍由现有管理流程设置，不由本部署脚本自动修改。

Caddy 是唯一可信反代，覆盖客户端转发头并终止 HTTPS。生产 Cookie 强制 Secure，CSRF 信任本站 SITE_URL；HTTP 只作跳转。Web/WebSocket 的健康探针只检查 TCP 监听，业务验收仍需实际请求和登录。worker/Beat 的业务健康以日志、任务成功和队列消费检查为准。反代只公开 `/static` 和 `/media`，不公开私有作品或分片目录。

## S3 配置（OSS / COS / MinIO）

将 `EVY_HACKATHON_STORAGE_BACKEND=s3`，设置 Endpoint、Region、Bucket、专用非空 Prefix、Access Key、Secret Key。镜像包含 deployment/requirements-s3.txt 固定 SDK；非容器开发仅启用 s3 时需要向自己的隔离环境安装此文件，本地开发的 app/uv.lock 无需变更。

OSS/COS 使用对应区域的 S3 兼容 HTTPS endpoint 和正确 bucket（COS 通常含 APPID），默认 virtual addressing；MinIO 通常用 path。只需要 PutObject/GetObject/HeadObject/DeleteObject/ListBucket（限制 prefix），不设置 ACL、不自动创建 bucket、不产生公网永久 URL。启用 bucket 私有访问、版本控制和云侧备份策略。AWS 签名及新增校验行为在兼容服务间有差异，真实 OSS/COS API 验收仍必需。凭据可用环境或 EVY_ 同名 secret 文件注入，优先级沿用现有配置系统。

下载仍经业务鉴权后由应用流式代理，支持 HEAD、单 Range、If-Range 和视频预览；S3 使用 Range GET，不把整段视频缓存在内存中。校验值仍为原文件 SHA-256，绝不用 ETag 代替。新对象为唯一键；写入失败不切换数据库引用；事务回滚产生的孤儿交给清理工具，保留分片便于重试。替换提交后旧文件删除失败只记日志，不让已提交事务表现为失败。

## 私有文件迁移、核验与回退

迁移期间停止 proxy/web/websocket/worker/beat，以及任何其他写入者和清理定时任务；禁止并行迁移和清理。先备份数据库、整个 data 卷、S3 版本/快照。为迁移命令配置目标 S3 凭据，暂时保留原 backend 配置。

```bash
docker compose stop proxy web websocket worker beat
docker compose run --rm web python manage.py migrate_hackathon_storage --source local --target s3 --copy
docker compose run --rm web python manage.py migrate_hackathon_storage --source local --target s3
# 两次全部校验通过后才改 .env backend=s3，再启动应用。
docker compose up -d --no-build web websocket worker beat proxy
```

命令遍历数据库引用的文件，按大小和 SHA-256 核验源和目标，保留对象名、不修改数据库、不删除源文件；缺失或冲突立即失败，可修复后重跑。默认仅验证；`--copy` 只复制不存在的目标。普通媒体和分片随 data 卷迁移。切换存储配置后必须重新创建/重启全部 Web、WebSocket 和 worker 进程（存储实例在进程内缓存）。切回 local 前停止所有写入者，反向运行 `--source s3 --target local --copy` 再仅验证，避免丢失切换后新增作品。数据库 schema 回退另需对应镜像兼容性检查或备份恢复，不能仅切镜像假定安全。

```bash
docker compose run --rm web python manage.py cleanup_hackathon_uploads --dry-run
# 核实候选列表后执行同命令（去掉 --dry-run）。不要在迁移/恢复期间运行。
```

清理分片和最终文件分别处理。仅扫描专用 prefix 下 48 小时前无数据库引用的对象；保留有效上传的分片。不要将其它业务文件放入该 prefix。版本控制 bucket 的 DeleteObject 可能只建立删除标记，历史版本成本由独立保留策略管理。

## 备份、恢复和验收

在 deployment 目录执行 `bash backup.sh /absolute/new-directory`。脚本停止写入、备份 DB/data、记录镜像及相对路径校验和，任何结果都保持写入者停止；随后完成云侧 S3 快照/版本记录与证书备份。确认所有备份完成再显式启动服务。先处理任务队列：暂停新任务，等待 worker 正在执行的任务完成，再备份；Redis AOF/队列的一致性需另做主机卷快照，不能盲目恢复旧队列重放外部副作用。备份目录权限 700，另行安全保存 .env 和镜像 digest，不公开上传备份。

恢复演练使用新的隔离 Compose project/空卷、隔离域名和邮件沙盒：启动 db/redis 后运行 `bash restore.sh /absolute/backup-directory`。恢复拒绝非空数据库/data，失败可能留下部分恢复结果，应丢弃隔离卷后重试。脚本不自动启动应用。S3 必须从对应快照恢复到隔离 bucket，恢复的同一存储内数据应通过读取数据库每个引用并计算大小/SHA-256核验；也可利用隔离的另一后端运行迁移命令交叉校验，再 init 静态文件、按镜像要求迁移 schema 并启动。SHA256SUMS 验证传输完整性，不证明数据库/业务恢复成功。

资源就位后逐项留下环境、镜像和结果：登录/报名/组队；分片中断续传、重复分片/完成请求；最终提交和截止边界；主办方补录；跨用户/跨队下载拒绝；HEAD/Range/预览；替换后旧文件保留/清理；对象存储断连后重试；容器重启；DB/data/S3 备份恢复；并发槽位与磁盘不足。使用合成文件，记录错误率、吞吐与资源使用。未完成这些检查前维持 Draft PR，不标记正式可用。


## 自动与手动验证入口

`.github/workflows/cloud-deployment.yml` 独立于 Activity CI，检查 Compose、构建本校生产镜像，并在 PostgreSQL + MinIO 隔离环境跑本地/S3 上传替换、下载访问、Range、失败重试和清理测试。CI 配置已添加，提交 PR 后以实际运行结果为准，不把尚未执行的配置视作通过。

离线轻量检查（不连接数据库/云）：`PYTHONPATH=app <隔离环境>/bin/python -m unittest discover -s deployment -p test_storage.py -v`。

手动 MinIO：以 `compose.minio.yml` 的 storage-test profile 启动隔离服务，使用测试 SDK 创建 `ruc-storage-test` bucket；访问凭据固定为测试值 test-access/test-secret-key，设置 `HACKATHON_TEST_S3_ENDPOINT=http://127.0.0.1:9000`。使用独立 PostgreSQL 并设置 CI_POSTGRES_* 后，从 app 运行 `pytest --ds=tests.hackathon.ci_settings tests/hackathon/test_cloud_storage.py`；没有 endpoint 时 S3 测试明确 skip，不能记为通过。不要把这些测试指向正式 bucket。

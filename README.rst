黑客松一体化平台 · 基于 Eventyay
========================================

本仓库是在 `Eventyay <https://github.com/fossasia/eventyay>`_ 基础上开发的黑客松定制版本，
把参赛报名、学习资料、组队、作品提交、现场路演、评委打分和主办方管理放在同一个网站中。
支持电脑、平板和手机访问，各端保留相同的业务功能。

**首次使用：** 阅读下方「功能简介」和「如何启动」。已有运行环境时，直接执行「启动与访问」中的命令。
完整操作说明见 `黑客松工作区文档 <doc/hackathon-workspace.md>`_。

功能简介
--------

* **参赛者：** 学号注册与登录、报名信息填写、关键词和一句话自我介绍、寻找队友与组队、
  查看教程、公告和赛程、提交作品、查看提交状态与成绩。学号注册不等同于学校统一身份认证。
* **作品与文件：** 上传海报、演示文稿、视频和源代码等材料；海报上限 20 MiB，
  其他支持类型的单文件上限 512 MiB。采用分片上传、校验、重试和续传，
  自动命名与归档，处理重复请求和并发提交；支持截止前排队提交与凭证。
  文件保存在私有磁盘目录，按权限访问，数据库保存关联信息。
* **主办方：** 管理报名和团队信息、审核作品、检查路演材料、维护教程和公告、
  管理工作人员与评委、导出数据和作品材料、安排路演队列与现场计时、发布结果。
* **评委：** 独立登录入口；评分页跟随当前路演团队，同页查看海报、作品描述和材料，
  也可回看其他团队。支持草稿与正式评分，以及自定义维度权重和去极值规则。
  默认按创新性、完成度与实机演示、实际价值、表达的 30% / 40% / 20% / 10% 加权，再取评委平均分。
* **现场协作：** 志愿者任务、签到与工时记录、求助处理、路演展示和作品展示。
* **多端界面：** 手机底部角色导航与单列布局，平板抽屉导航与适配布局，桌面完整工作台。

如何启动
--------

请使用**当前定制仓库的代码**。单独克隆上游 Eventyay 仓库不会包含这里的黑客松模块。
下面以活动标识 ``next`` 为例；更换 ``--slug`` 后，访问地址中的 ``next`` 也要相应替换。

首次准备
~~~~~~~~

可选择 Docker 或本地 Python 环境，两者不需要同时配置：

* **Docker：** 按 `Quick start with Docker`_ 配置并启动服务，该节包含活动初始化命令。
* **本地 Python：** 按 `Python based local development`_ 安装依赖、配置 PostgreSQL 和 Redis，
  完成数据库迁移与静态资源构建。然后在 ``app/`` 目录执行：

.. code-block:: bash

   . .venv/bin/activate
   python manage.py migrate
   python manage.py create_admin_user
   python manage.py setup_hackathon --organizer hackathon --event next-hackathon \
     --slug next --name '下一届黑客松' --publish

``create_admin_user`` 会提示创建管理员，请保存自己设置的登录信息；已有管理员时可跳过此命令。仓库不提供通用默认密码。
``setup_hackathon`` 创建活动、教程和赛程等初始内容，不创建虚构选手或作品。
未指定 ``--start-date YYYY-MM-DD`` 时，活动开始日期默认为执行日的 30 天后；正式开放前请检查日期与截止时间。
``--publish`` 使活动对参赛者可见。重复执行初始化不会覆盖已有活动配置。

启动与访问
~~~~~~~~~~

本地环境已配置好后，确认 PostgreSQL 和 Redis 正在运行，在仓库根目录执行：

.. code-block:: bash

   cd app
   . .venv/bin/activate
   python manage.py run_hackathon_preview 0.0.0.0:8000 --insecure

保持终端运行，浏览器访问 http://localhost:8000/hackathon/next/ 。按 ``Ctrl+C`` 停止预览。
这个预览命令使用 WSGI，以流式方式提供大文件下载；``--insecure`` 用于本地预览时提供静态文件。
生产部署请参考 `Deployment`_ 和 `黑客松工作区文档 <doc/hackathon-workspace.md>`_ 的存储与大文件部署说明。

手机或平板预览时，设备与服务器需能通过局域网互通，且服务器开放 8000 端口。
将以下地址替换为运行服务的电脑 IP，并在同一个终端设置后启动：

.. code-block:: bash

   export EVY_SITE_URL=http://192.168.1.100:8000
   export EVY_SHORT_URL=http://192.168.1.100:8000
   export EVY_TALK_HOSTNAME=http://192.168.1.100:8000
   python manage.py run_hackathon_preview 0.0.0.0:8000 --insecure

随后在移动设备打开 ``http://192.168.1.100:8000/hackathon/next/``。
移动设备上的 ``localhost`` 指向设备自身，不是运行网站的电脑。

登录入口与常用页面
~~~~~~~~~~~~~~~~~~

以下路径均相对于网站地址，例如 ``http://localhost:8000``：

* 活动首页：``/hackathon/next/``
* 参赛者注册：``/hackathon/next/account/signup/``
* 参赛者登录：``/hackathon/next/account/login/``
* 主办方登录：``/hackathon/next/account/organizer/``
* 主办方工作台：``/hackathon/next/manage/``
* 评委登录：``/hackathon/next/account/judge/``
* 评委现场评分：``/hackathon/next/judging/live/``
* 路演展示：``/hackathon/next/stage/``

首次主办方登录使用上面创建的管理员账号。主办方可在工作台创建评委账号或为已有用户分配活动角色；
普通参赛者注册不会自动获得主办方或评委权限。登录后可通过账户菜单退出。
黑客松管理请使用 ``/hackathon/next/manage/``，``/control/`` 和 ``/orga/`` 是原 Eventyay 管理界面。

数据保存与常见问题
~~~~~~~~~~~~~~~~~~

* **数据库连接失败：** 检查 PostgreSQL 是否启动，以及 ``app/eventyay.local.toml`` 或 ``EVY_POSTGRES_*``
  环境变量中的地址、端口、用户和数据库名。非默认端口必须显式配置。
* **活动不存在或不可见：** 确认执行过 ``migrate`` 和带 ``--publish`` 的 ``setup_hackathon``，并使用正确的活动标识。
* **页面无样式：** 本地预览使用上述带 ``--insecure`` 的命令；完整静态资源构建方法见下方开发说明。
* **HTTP 500：** 查看运行终端或 ``docker compose logs -f web`` 中的异常，先核对迁移和服务连接，
  不要用删除数据库的方式处理普通启动问题。
* **文件持久化：** 同时备份 PostgreSQL 和 ``DATA_DIR/hackathon-private/``，容器部署须为数据目录配置持久化存储。
  大文件访问还取决于磁盘空间、反向代理限制和超时配置，详见工作区文档。

项目声明与致谢
--------------

本仓库是面向黑客松场景的 Eventyay 衍生定制版本，不是 Eventyay 上游官方发行版，
也不表示获得 FOSSASIA 或 Eventyay 团队的背书。此处的黑客松功能、界面和使用说明属于本仓库的定制内容；
相关问题请在本仓库反馈。

感谢 `FOSSASIA <https://fossasia.org>`_、`Eventyay 项目及其贡献者 <https://github.com/fossasia/eventyay>`_
提供开源活动管理平台，以及账户、活动、权限、管理和部署等基础设施。
也感谢原项目所依赖的开源社区；历史来源与第三方署名见 `NOTICE <NOTICE>`_。

本仓库保留原项目版权与署名，根目录 `LICENSE <LICENSE>`_ 为 Apache License 2.0。
各组件和依赖的许可与声明以对应文件为准；本 README 不替代或变更这些许可。
上游通用功能、开发和部署资料保留在下文，黑客松特有行为以本仓库实现和工作区文档为准。

Contents
--------

- `功能简介`_
- `如何启动`_
- `项目声明与致谢`_
- `Project status and branch model`_
- `Main features`_
- `Technology stack`_
- `Quick start with Docker`_
- `Python based local development`_
- `Frontend development`_
- `Configuration overview`_
- `Plugins and extensions`_
- `Separate Eventyay components`_
- `Translation workflow`_
- `Common development commands`_
- `API and service endpoints`_
- `Repository layout`_
- `Architecture rules for contributors`_
- `Troubleshooting`_
- `Deployment`_
- `Documentation`_
- `Contributing`_
- `Legal and licensing`_

Project status and branch model
-------------------------------

This school repository adds a hackathon workspace to Eventyay. ``main`` is the school's default integration branch. Create focused ``feat/``, ``fix/`` or ``docs/`` branches from ``origin/main`` and open PRs against ``Exyeus/RUC-Makers-Platform:main``.

``upstream/dev`` is read-only upstream reference, and ``rucmaker`` preserves history. Keep the server's unpublished ``work/server-development`` work separate; do not switch, merge or reset its dirty worktree. A branch setting does not establish that deployment or a new environment has been verified.

See the `School collaboration entry <.agent/README.md>`_ and `Branch isolation guide <.agent/BRANCHES.md>`_ for task handoff, review and clean personal checkout steps.

Main features
-------------

Eventyay includes:

* Event ticketing and registration
* Event related item sales, such as shirts or add ons
* Organiser and event administration
* Call for Participation workflows
* Speaker, submission, review, and schedule management
* Public event pages
* Schedule display and schedule editor frontends
* Online event and video related workflows
* Separate check-in station support through the Eventyay Checkin component
* PDF ticket and badge related workflows
* REST API endpoints
* OAuth and social authentication support
* Multi domain and multi event handling
* Plugin discovery and plugin URL registration
* Standard plugins and external plugin extensions for payments, CRM, exhibitions, social media, team shifts, interpretation, and spatial event integrations
* Email, notifications, scheduled tasks, reports, statistics, and check-in lists
* Health check and metrics endpoints
* Internationalisation and translation infrastructure with Hosted Weblate based browser translation workflow

Technology stack
----------------

Backend:

* Python 3.12
* Django (version pinned in ``app/uv.lock``)
* Django REST Framework
* PostgreSQL
* Redis
* Celery
* Django Channels
* Daphne / ASGI
* Pydantic settings
* uv for Python dependency management

Frontend:

* Vue 3
* Vite
* JavaScript and TypeScript depending on the frontend module
* SCSS / Stylus
* Django Compressor for selected legacy and Django integrated assets

Quick start with Docker
-----------------------

Docker is the recommended way to start quickly for local development and first testing. For production or self-hosted server deployment, see the `Deployment`_ section below and ``DEPLOYMENT.md``.

The local Docker stack starts the Django web service, Celery worker, Celery beat, Redis, and PostgreSQL. It also mounts ``./plugins`` for local plugin development.

For hackathon file-upload and download previews, create ``docker-compose.override.yml`` in the repository root with the following override before starting the stack. This uses the WSGI preview command instead of the default ASGI development server:

.. code-block:: yaml

   services:
     web:
       command: python manage.py run_hackathon_preview 0.0.0.0:8000 --insecure

Requirements:

* Docker
* Docker Compose plugin
* Git

Steps:

.. code-block:: bash

   # Run from the root of this customized repository.

   cp deployment/env.dev.sample .env.dev

   docker compose up -d --build

Create an admin user and initialize the hackathon workspace (after the web service has completed its startup migrations):

.. code-block:: bash

   docker compose exec web python manage.py create_admin_user
   docker compose exec web python manage.py setup_hackathon --organizer hackathon \
     --event next-hackathon --slug next --name '下一届黑客松' --publish

Open the local site:

.. code-block:: text

   http://localhost:8000/hackathon/next/

View logs:

.. code-block:: bash

   docker compose logs -f

Stop the development stack:

.. code-block:: bash

   docker compose down

The directory ``app/eventyay`` is mounted into the Docker container, so live editing of backend code is supported.

Python based local development
------------------------------

Use this setup when you want to run services directly on your machine.

Requirements:

* Python 3.12
* `uv <https://docs.astral.sh/uv/getting-started/installation/>`_
* PostgreSQL
* Redis
* Node.js and npm
* Debian or Ubuntu packages listed in ``deb-packages.txt`` or equivalent packages for your distribution

Run the following setup steps from the root of this customized repository.

Install external dependencies on Debian or Ubuntu:

.. code-block:: bash

   xargs -a deb-packages.txt sudo apt install

For Nushell:

.. code-block:: nu

   open deb-packages.txt | lines | sudo apt install ...$in

If you are using another Linux distribution, install the corresponding packages from ``deb-packages.txt``.

Install `uv <https://docs.astral.sh/uv/getting-started/installation/>`_.

Install and run Redis according to your distribution.

Create a PostgreSQL database. The default local database name is:

.. code-block:: text

   eventyay-db

On Linux, the simplest local development setup is PostgreSQL peer mode. Create a PostgreSQL user with the same name as your Linux user:

.. code-block:: bash

   sudo -u postgres createuser -s "$USER"

Then create a database owned by your user:

.. code-block:: bash

   createdb eventyay-db

You can then access the database without specifying a password, host, or port:

.. code-block:: bash

   psql eventyay-db

If you cannot use PostgreSQL peer mode, create ``app/eventyay.local.toml`` with database connection values:

.. code-block:: toml

   postgres_user = "your_db_user"
   postgres_password = "your_db_password"
   postgres_host = "localhost"
   postgres_port = 5432

Enter the app directory:

.. code-block:: bash

   cd app

Install Python dependencies:

.. code-block:: bash

   uv sync --all-extras --all-groups

Activate the virtual environment:

.. code-block:: bash

   . .venv/bin/activate

Run migrations:

.. code-block:: bash

   python manage.py migrate

Create an admin user:

.. code-block:: bash

   python manage.py create_admin_user
   python manage.py setup_hackathon --organizer hackathon --event next-hackathon \
     --slug next --name '下一届黑客松' --publish

Build frontend and static assets:

.. code-block:: bash

   make npminstall
   python manage.py collectstatic --noinput
   python manage.py compress --force

Run the development server:

.. code-block:: bash

   python manage.py run_hackathon_preview 0.0.0.0:8000 --insecure

Open:

.. code-block:: text

   http://localhost:8000/hackathon/next/

Run Celery locally when working on background tasks:

.. code-block:: bash

   celery -A eventyay worker -l info

Mobile testing note
~~~~~~~~~~~~~~~~~~~

If you want to test the site from an Android emulator, use:

.. code-block:: text

   http://10.0.2.2:8000/

This is Android's alias for the host machine's localhost.

Permission note
~~~~~~~~~~~~~~~

If you get permission errors for ``eventyay/static/CACHE``, make sure that the directory and all files below it are owned by your user.

Frontend development
--------------------

The repository contains several frontend applications under ``app/eventyay/webapp/``:

.. code-block:: text

   app/eventyay/webapp/
   ├── schedule/              Schedule display web component
   ├── schedule-editor/       Schedule editor frontend
   └── video/                 Online event video frontend

The root app ``Makefile`` installs and builds these frontend applications through npm and places compiled assets into the Django app data directory.

Check-in uses the separate ``eventyay-checkin`` plugin and is not built by the Makefile ``npminstall`` target.

By default, Docker serves prebuilt frontend assets. To enable hot module replacement for frontend development, set this in ``.env.dev``:

.. code-block:: bash

   EVY_NPM_DEV=1

When ``EVY_NPM_DEV=1`` in ``.env.dev``, the Docker image build skips ``make npminstall``, and Vite dev servers start automatically inside the container, install npm dependencies as needed, and serve the webapps with HMR.

- ``schedule-editor`` runs on port ``8080``.
- ``video`` runs on port ``8880``.
- ``schedule`` runs on port ``8082``.
- ``eventyay-checkin`` runs on port ``8085`` when the plugin checkout is mounted at ``plugins/eventyay-checkin``.

You do not normally need to visit these ports directly. The frontend works alongside ``http://localhost:8000`` with hot module replacement.

The container must be recreated, not only restarted, for the environment change to take effect:

.. code-block:: bash

   docker compose up -d --build web

The default is:

.. code-block:: bash

   EVY_NPM_DEV=0

With ``EVY_NPM_DEV=0``, Django serves prebuilt assets, so run ``make npminstall`` after frontend changes. Before submitting frontend changes, always verify that the production asset build still works with the default value:

.. code-block:: bash

   docker exec -ti eventyay-next-web make npminstall
   docker exec -ti eventyay-next-web python manage.py collectstatic --noinput

Configuration overview
----------------------

The Eventyay configuration is based on TOML files, environment variables, dotenv files, and secret files. To see possible configuration keys and default values, check the ``BaseSettings`` class in ``app/eventyay/config/settings.py``.

The configuration is divided into three running environments:

- ``development``: Uses default values from ``eventyay.development.toml``.
- ``production``: Uses default values from ``eventyay.production.toml``.
- ``testing``: Uses default values from ``eventyay.testing.toml``.

The values in these files override values defined in ``BaseSettings``.

The running environment is selected via the ``EVY_RUNNING_ENVIRONMENT`` environment variable. It is pre-set in ``manage.py``, ``wsgi.py``, and ``asgi.py``.

For example, to run a command in the production environment:

.. code-block:: bash

   EVY_RUNNING_ENVIRONMENT=production ./manage.py command

Configuration sources are loaded with the following precedence:

1. Secret files in ``.secrets/``
2. Environment variables with the ``EVY_`` prefix
3. ``.env`` in the current working directory
4. ``eventyay.local.toml``
5. Environment specific TOML files such as ``eventyay.development.toml``, ``eventyay.production.toml``, or ``eventyay.testing.toml``

How to override configuration values
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Create a file named ``eventyay.local.toml`` in the same folder as the ``manage.py`` file.

Add only the values you want to override. For example, to override the ``debug`` value, add:

.. code-block:: toml

   debug = true

For database connection values, a local override can look like this:

.. code-block:: toml

   postgres_user = "your_db_user"
   postgres_password = "your_db_password"
   postgres_host = "localhost"
   postgres_port = 5432

For complex values such as lists, prefer TOML files over environment variables.

You can also override values via environment variables. Environment variable names are the uppercase versions of the setting keys, prefixed by ``EVY_``.

For example, to override the ``debug`` value:

.. code-block:: bash

   export EVY_DEBUG=true

A dotenv file, ``.env``, is also supported. Values from ``.env`` are overridden by environment variables.

Sensitive data such as passwords and API keys should be provided via files in the ``.secrets`` directory, one file per key. The file name follows the same pattern as environment variable names with the ``EVY_`` prefix. The file content is the value.

For example, to provide a value for the ``secret_key`` setting, create this file:

.. code-block:: text

   .secrets/EVY_SECRET_KEY

If you deploy the app via Docker containers, you can provide secret data through `Docker secrets <https://docs.docker.com/engine/swarm/secrets/>`_.

Geocoding
~~~~~~~~~

In development, Nominatim geocoding is used automatically when no OpenCage or MapQuest key is configured. Production deployments should configure a geocoding provider or explicitly enable public Nominatim geocoding in global settings.

Email configuration for testing
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

By default, emails are printed to the terminal logs through the console backend.

To test email related features with real delivery, configure a mail server such as SendGrid, Gmail SMTP, or another SMTP provider.

For Gmail SMTP, use:

.. code-block:: text

   Host: smtp.gmail.com
   Port: 587
   TLS: enabled

If 2FA is enabled on the Google account, you may need to use an App Password.

Plugins and extensions
----------------------

Eventyay comes with several plugins that are part of the standard setup. These built-in plugins provide common functionality for authentication, reports, badges, check-in workflows, scheduled tasks, statistics, and ticket outputs.

Built-in and standard plugin areas include:

- Authentication and social auth
- Bank transfer
- Badges
- Sendmail
- Statistics
- Reports
- Check-in lists
- Manual payment
- Return URLs
- Scheduled tasks
- PDF ticket output

Eventyay also supports external plugin extensions. These plugins can be installed to add integrations or event specific functionality.

Available plugin extensions include:

- `eventyay-exhibition <https://github.com/fossasia/eventyay-exhibition>`_: Exhibition component
- `eventyay-interpretation <https://github.com/fossasia/eventyay-interpretation>`_: Integration with interpretation services such as Voxbento
- `eventyay-hubspot <https://github.com/fossasia/eventyay-hubspot>`_: HubSpot integration
- `eventyay-loungemesh <https://github.com/fossasia/eventyay-loungemesh>`_: Loungemesh integration
- `eventyay-socialmedia <https://github.com/fossasia/eventyay-socialmedia>`_: Social media sharing and publishing integration
- `eventyay-teamshifts <https://github.com/fossasia/eventyay-teamshifts>`_: Team shifts and volunteer shift management plugin

Available payment plugins are:

- `eventyay-bitpay <https://github.com/fossasia/eventyay-bitpay>`_: BitPay crypto payment integration
- `eventyay-paypal <https://github.com/fossasia/eventyay-paypal>`_: PayPal payment integration
- `eventyay-stripe <https://github.com/fossasia/eventyay-stripe>`_: Stripe payment integration

To create a new Eventyay plugin, use the `eventyay plugin cookiecutter template <https://github.com/fossasia/eventyay-plugin-cookiecutter>`_.

Eventyay discovers compatible plugins through installed Django apps and Python entry points. The platform can register plugin URL patterns automatically when plugin metadata and URL modules are available.

Developing plugins locally
~~~~~~~~~~~~~~~~~~~~~~~~~~

If you are developing plugins, create a directory named ``plugins`` in the root of this repository. This directory is gitignored and can contain local plugin checkouts.

Example:

.. code-block:: text

   .
   └── eventyay/
       └── plugins/
           ├── eventyay-exhibition/
           ├── eventyay-interpretation/
           └── eventyay-checkin/

Local plugin development is controlled by two flags in ``.env.dev``:

* ``EVY_PLUGIN_DEV_MODE=1`` (default): scans ``./plugins/`` and installs Python plugins in editable mode so code changes reflect instantly. If ``EVY_NPM_DEV=1`` and ``./plugins/eventyay-checkin`` exists, its Vite dev server runs on port ``8085``.
* ``EVY_PLUGIN_DEV_MODE=0``: skips editable installs and uses locked image dependencies.
* ``EVY_NPM_DEV=1`` (default): starts Vite HMR dev servers on ports ``8080``, ``8082``, ``8880`` for live frontend changes.
* ``EVY_NPM_DEV=0``: serves pre-built static assets instead.

.. note::

   Changing ``EVY_PLUGIN_DEV_MODE`` or ``EVY_NPM_DEV`` requires a container restart to take effect::

      docker compose restart web worker beat

   No image rebuild is needed. Both flags have no effect when ``EVY_RUNNING_ENVIRONMENT`` is not ``development``.

Separate Eventyay components
----------------------------

Eventyay also provides separate components that can be used together with the main platform.

Eventyay Checkin
~~~~~~~~~~~~~~~~

`Eventyay Checkin <https://github.com/fossasia/eventyay-checkin>`_ is a separate check-in component for kiosk stations. It enables organisers to check in attendees at dedicated check-in stations during an event.

Eventyay Interpretation
~~~~~~~~~~~~~~~~~~~~~~~

`Eventyay Interpretation <https://github.com/fossasia/eventyay-interpretation>`_ is a separate component for live interpretation. It enables multi-language audio streaming for sessions in an event.

Translation workflow
--------------------

The Eventyay platform can be translated through `Hosted Weblate <https://hosted.weblate.org/projects/eventyay/eventyay/>`_.

Contributors can use the Weblate workflow to add or improve translations directly in the browser. This means translation contributors do not need to check out the repository, edit translation files manually, or work inside the codebase.

Developers who maintain translation files in the repository can still use the local translation commands listed below.

Common development commands
---------------------------

Run from ``app/`` unless stated otherwise.

Install dependencies:

.. code-block:: bash

   uv sync --all-extras --all-groups

Activate virtual environment:

.. code-block:: bash

   . .venv/bin/activate

Apply database migrations:

.. code-block:: bash

   python manage.py migrate

Run development server:

.. code-block:: bash

   python manage.py run_hackathon_preview 0.0.0.0:8000 --insecure

Run Celery worker:

.. code-block:: bash

   celery -A eventyay worker -l info

Build development static files:

.. code-block:: bash

   make staticfiles

Build production assets:

.. code-block:: bash

   make production

Run tests:

.. code-block:: bash

   pytest tests/

Compile translations:

.. code-block:: bash

   make localecompile

Regenerate translation files:

.. code-block:: bash

   make localegen

Build JavaScript translation catalogues and static files:

.. code-block:: bash

   make staticfiles

API and service endpoints
-------------------------

Important local endpoints include:

.. code-block:: text

   /hackathon/               Hackathon workspace entry
   /hackathon/next/manage/    Hackathon organiser workspace
   /                         Public start page
   /orga/                    Organiser area for talks and speakers configuration and setup
   /control/                 Ticketing control and organiser management area
   /admin/                   Platform admin interface
   /api/v1/                  REST API
   /healthcheck/             Health check
   /metrics/                 Metrics endpoint
   /accounts/                Account and authentication routes
   /<organizer>/             Organizer public routes
   /<organizer>/<event>/     Event public routes
   /<organizer>/<event>/cfp/ Call for Participation routes
   /<organizer>/<event>/video/ Online event video frontend

Repository layout
-----------------

Important paths:

.. code-block:: text

   .
   ├── app/
   │   ├── eventyay/          Main Eventyay application code
   │   ├── tests/             Backend and integration tests
   │   ├── tools/             App related helper tools
   │   ├── manage.py          Django management entry point
   │   ├── pyproject.toml     Python project metadata and dependencies
   │   └── Makefile           Asset, translation, and test helper commands
   ├── deployment/            Docker, deployment, environment, and nginx related files
   ├── doc/                   Project documentation sources
   ├── tools/                 Repository level tools
   ├── docker-compose.yml     Local Docker development stack
   ├── CONTRIBUTING.md        Contribution workflow
   ├── DEPLOYMENT.md          Production and self-hosted deployment guide
   ├── CLA.md                 Contributor License Agreement information
   ├── LICENSE                Apache License 2.0
   └── NOTICE                 Attribution and upstream notices

Main backend modules are under ``app/eventyay/``. Important areas include:

.. code-block:: text

   app/eventyay/
   ├── agenda/                Public schedule and agenda functionality
   ├── api/                   API endpoints and OAuth models
   ├── base/                  Core user, auth, middleware, and shared base code
   ├── cfp/                   Call for Participation functionality
   ├── common/                Shared platform utilities
   ├── config/                Django settings, ASGI, WSGI, and URL configuration
   ├── control/               Ticketing control and organiser management area
   ├── event/                 Event domain logic
   ├── features/              Feature modules such as analytics, live, importers, social, integrations
   ├── mail/                  Email handling
   ├── multidomain/           Multi domain routing and middleware
   ├── orga/                  Talks and speakers organiser area
   ├── person/                User and person related functionality
   ├── plugins/               Built in plugins
   ├── presale/               Ticketing and public event registration
   ├── schedule/              Schedule related functionality
   ├── storage/               File and media storage handling
   ├── submission/            Submission handling
   └── webapp/                Vue/Vite frontend applications

Architecture rules for contributors
-----------------------------------

Important rules for changes:

- Product code belongs under ``app/eventyay/``.
- Tests belong under ``app/tests/``.
- Documentation belongs under ``doc/``.
- Event owned ORM queries must be scoped correctly with ``django_scopes.scope(event=event)``.
- Keep imports at the top of files unless a local import is required to avoid a circular import.
- Preserve specific exception types instead of replacing them with generic ``Exception``.
- Do not introduce new jQuery usage or inline scripts.
- Prefer external ES modules for JavaScript.
- Use ``select_related`` and ``prefetch_related`` where appropriate to avoid N+1 queries.

Troubleshooting
---------------

CSS not loading or MIME type errors
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

In some environments, for example Docker with WSL, pages may load without CSS or only some pages may load correctly.

Browser console errors may look like:

.. code-block:: text

   Refused to apply style because its MIME type is 'text/html'

This usually means a static asset was requested but an HTML response, such as a 404 page, was returned instead.

Rebuild static assets:

.. code-block:: bash

   docker exec -ti eventyay-next-redis redis-cli FLUSHDB
   docker exec -ti eventyay-next-web rm -rf /usr/src/app/eventyay/static.dist/CACHE/css/*
   docker exec -ti eventyay-next-web make npminstall
   docker exec -ti eventyay-next-web python manage.py collectstatic --noinput
   docker exec -ti eventyay-next-web python manage.py compress --force
   docker restart eventyay-next-web

Then hard refresh the browser with ``Ctrl + Shift + R``.

Database issues in Docker
~~~~~~~~~~~~~~~~~~~~~~~~~

The database in the development Docker setup is stored in the ``eventyay-next_postgres_data_dev`` Docker volume. If you see errors concerning login or other database related behaviour, you can completely reset the database.

You will lose all local configuration, organisers, events, users, tickets, and related database content.

You must stop the stack first so no container is using the volume. Otherwise, ``docker volume rm`` will fail. ``docker compose down`` stops and removes the containers but keeps named volumes by default.

.. code-block:: bash

   docker compose down
   docker volume rm eventyay-next_postgres_data_dev

Redis issues in Docker
~~~~~~~~~~~~~~~~~~~~~~

Redis data in the development Docker setup is stored in the ``eventyay-next_rd`` volume. This corresponds to the ``rd`` volume in ``docker-compose.yml``.

If you see connection, cache, broker, or background task related errors, you can reset Redis the same way: stop the stack, then remove the volume.

You will lose data held in Redis, such as cache or broker state.

.. code-block:: bash

   docker compose down
   docker volume rm eventyay-next_rd

Reset PostgreSQL and Redis together
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

To wipe both PostgreSQL and Redis data in one go, you can run ``docker compose down -v`` instead of removing volumes individually. This removes all named volumes declared for this Compose project, including ``eventyay-next_postgres_data_dev`` and ``eventyay-next_rd``.

.. code-block:: bash

   docker compose down -v

After resetting PostgreSQL and/or Redis
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Bring the development stack back up in detached mode with a rebuild:

.. code-block:: bash

   docker compose up -d --build

Deployment
----------

The Docker quick start in this README is intended for local development and first testing. For production or self-hosted server deployments, use the dedicated deployment guide:

`DEPLOYMENT.md <DEPLOYMENT.md>`_

The deployment guide covers server setup, Docker, Docker Compose, nginx, certbot, PostgreSQL data storage, static file handling, deployment specific environment configuration, backups, and maintenance related setup.

Operators should review and adapt the deployment instructions for their own infrastructure, backup strategy, monitoring, TLS setup, mail delivery, payment providers, and security requirements.

Documentation
-------------

Full Eventyay documentation is available at:

https://docs.eventyay.com

Useful entry points:

- `Hackathon workspace guide <doc/hackathon-workspace.md>`_

- `Deployment guide <DEPLOYMENT.md>`_
- `Contribution guide <CONTRIBUTING.md>`_
- `Contributor License Agreement <CLA.md>`_
- `License <LICENSE>`_

The documentation website should be used for the full user guide, platform administration documentation, developer documentation, API documentation, and reference material.

Continuous integration
----------------------

CI is not being implemented for the school workflow now. Existing GitHub Actions are disabled in the school repository settings; inherited workflow files remain upstream reference, not an automatic review or merge gate.

Before opening a pull request, run targeted self-checks for the files and risks you changed, record actual results and unverified items, and obtain one person's review. Substantive activity-platform changes use the `Adversarial audit skill <.agent/skills/adversarial-audit/SKILL.md>`_; tiny copy/style edits may use quick human review.


Contributing
------------

We welcome contributions.

Basic workflow:

1. Use the school repository as ``origin`` in a clean personal checkout.
2. Create a focused ``feat/``, ``fix/`` or ``docs/`` branch from ``origin/main``.
3. Make focused changes.
4. Add or update tests when needed to verify changed behaviour.
5. Run targeted local checks and record results and unverified items.
6. Open a pull request against the school ``main`` branch and obtain one person's review.

Pull request expectations:

- Link the PR to its main task card or requirement.
- If there is a GitHub issue, use closing keywords such as ``Fixes #123`` in the PR description.
- Keep PRs small enough to review in less than a day where possible.
- Add screenshots or short videos for UI changes.
- Open draft PRs early for large or long running work.
- Respond to review comments and keep the branch up to date.
- Do not repeatedly tag reviewers.

See `CONTRIBUTING.md <CONTRIBUTING.md>`_ for the full contribution guidelines.

AI assisted development
~~~~~~~~~~~~~~~~~~~~~~~

This repository includes structured guidance for AI assisted development.

AI tools should consult:

- ``agents.md``
- ``.github/instructions/``
- ``.agents/skills/``
- ``.agent/README.md`` for the school task, branch and review workflow

These files define repository specific coding, architecture, documentation, and pull request expectations.

Future improvements
-------------------

Backend
~~~~~~~

Potential backend improvements include:

- Apply more Python type annotations.
- Add MyPy, ty, or another type checker.
- Improve IDE autocomplete and early bug detection through typing.
- Continue moving selected templating workflows toward Jinja where useful.
- Use djlint or another formatter to clean up template code.

Frontend
~~~~~~~~

Potential frontend improvements include:

- Remove remaining jQuery code.
- Convert older frontend behaviour to Vue or AlpineJS.
- Continue evaluating Single Page Application architecture where it makes sense.
- Use TypeScript more consistently where it improves maintainability.
- Consider HTMX and AlpineJS where Django rendered HTML remains the preferred approach.

Support
-------

Eventyay is free and open source software.

The following support information refers to upstream Eventyay services, not support commitments for this customized hackathon repository. Professional support is available to customers of the hosted Eventyay service or Eventyay enterprise offerings.

For commercial support, hosting services, custom development, deployment support, integrations, or financial support of the project, visit `eventyay.com <https://eventyay.com>`_.

Legal and licensing
-------------------

Eventyay is published under the Apache License 2.0. See `LICENSE <LICENSE>`_ for the complete license text.

See `NOTICE <NOTICE>`_ for attribution and upstream project notices.

Contributions are accepted under the Apache License 2.0. See `CONTRIBUTING.md <CONTRIBUTING.md>`_ and `CLA.md <CLA.md>`_ for details.

The upstream Eventyay project is maintained by `FOSSASIA <https://fossasia.org>`_. This customized repository is a derivative project; see `项目声明与致谢`_.

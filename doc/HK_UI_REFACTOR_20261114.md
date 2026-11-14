# 香港站 UI 组件重构（2026-11-14）

本轮保留宣传页紫色配色、粒子星空、工作台深色与淡紫色配色。Django 继续负责页面内容、权限、表单校验和 POST 操作。

## 组件与规范

- 账户菜单及其他身份登录入口使用真实 shadcn/ui DropdownMenu、Button、Separator；移动端全部导航使用 shadcn/ui Sheet。组件源码、上游参考、MIT 许可、依赖锁文件位于 `app/eventyay/webapp/hackathon-ui/`。
- 服务端渲染的表单、卡片、按钮、筛选、表格和状态提示保留其原生语义，以 `ui-refactor.css` 统一布局、字号、间距、圆角和触控尺寸。这些服务端控件没有被宣称为 React 组件。
- 依据 Apple HIG 的内容优先级、关联分组、渐进披露、响应式布局和可访问性原则，整理全局导航、账户操作、组队入口及聊天界面。
- 菜单和抽屉支持键盘操作、Escape 关闭及焦点恢复；基础页面仍由服务器输出。无脚本时账户菜单保留原生 details，全部导航有 noscript 入口。

设计参考：[shadcn/ui](https://github.com/shadcn-ui/ui)、[Apple HIG Layout](https://developer.apple.com/design/human-interface-guidelines/layout)、[Apple HIG Accessibility](https://developer.apple.com/design/human-interface-guidelines/accessibility)。

## 构建

在 WSL2 Ubuntu 的同一源码树中，进入 `app/eventyay/webapp/hackathon-ui/`，执行 `npm ci`、`npm run build`。Windows 与 Linux 的 node_modules 不共用。构建后的 `ui.js` 和 `ui-components.css` 由站点本地静态服务提供，线上运行不依赖 npm 或第三方 CDN。

## Git 与恢复

- 重构前基线：`ec0ed3e96ebfe808aa851b8ff7a1afd39d94ddd2`。
- 回退标签：`backup/hk-ui-before-20261114`。
- 本轮分支：`refactor/hk-shadcn-ui-20261114`。
- 发布证据、验证结果、逐文件 manifest 与恢复脚本位于工作区 `ui-refactor-hk-20261114/`；服务器发布证据目录为 `/srv/ruc-makers/ops/ui-refactor-20261114/`。

本轮仅涉及界面源码与静态资源，不包含数据库迁移。生产恢复应使用本轮逐文件备份并核对后续改动；不要对有现场差异的服务器执行整库恢复或 `git reset --hard`。`rollback.py` 会拒绝覆盖本轮上线后又被修改的文件，保留账号、组队、聊天与提交数据。

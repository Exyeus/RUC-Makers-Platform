# 香港站浅色外观（2026-11-15）

新增“浅色 / 深色 / 跟随系统”外观菜单，默认跟随系统；手动选择保存在当前浏览器的 `rucmakers-theme` localStorage 中，跨页面及同源标签页同步。选择外观不提交业务表单、不修改账号资料。

## 设计与组件

- 使用现有 shadcn/ui Button、DropdownMenuRadioGroup 与 DropdownMenuRadioItem，保留键盘操作、焦点恢复和 44px 触控目标。
- 页面背景为浅薰衣草 `#f1eafa` → **白色 50%** → 浅玫瑰 `#fcebf2`；卡片使用带色浅底。保留紫色与红/玫瑰色强调。新增的四处渐变均含白色 50% 中间停靠点，主要按钮使用实色以保证文字对比度。
- 浅色下使用已有红色协会标识与活动图形，活动文字独立排版；没有对页面或图片全局反相。原有深色配色保留。
- 覆盖活动入口、账号页、工作台、三个队伍/想法页面、匹配、私聊、管理、路演、退订和错误页；颜色不作为唯一状态信息。

参考：[shadcn/ui](https://github.com/shadcn-ui/ui)、[shadcn mode toggle](https://ui.shadcn.com/docs/dark-mode/vite)、[Apple HIG Color](https://developer.apple.com/design/human-interface-guidelines/color)。这是对网站的设计原则适配，不宣称是原生 Apple 界面。

## 实现

- `theme-init.js` 在第一份样式表前同步设置 `html[data-theme]`，避免已保存主题在首帧被另一主题覆盖。
- `theme.css` 中的配色覆盖限定在 `[data-theme='light']`；新增标识隐藏和控件布局为必要的两模式共享规则。
- `theme-menu.tsx` 为 React 增强；即使 ui.js 加载失败，早期脚本仍可启用原生选择器。反向情况下 ui.js 包含幂等启动逻辑，能够恢复早期脚本加载失败。禁用存储时当前页面仍能切换；完全禁用 JavaScript 时保留原深色页面与禁用的外观选择器。
- 事件 `rucmakers:themechange` 携带 `{preference,resolved}`；状态 API 见前端 README。
- 无新增依赖、数据库迁移或权限变动。本站提供构建资源，无第三方 CDN 运行依赖。

WSL 中进入 `app/eventyay/webapp/hackathon-ui/` 执行 `npm run check` 与 `npm run build`。运行逻辑回归位于 `app/tests/hackathon/theme_init_regression.cjs`，可用 `node --test` 执行。

## 验证与恢复

全量活动测试 675 passed、5 skipped、4 subtests；启动逻辑 12 项、浏览器 84 组页面/主题/尺寸、22 项交互和 17 项精查通过。浅色文字最坏抽样对比度 4.98:1、输入边框 3.61:1；这不是对所有未来内容的无限制可访问性认证。

Git 分支 `feat/hk-light-mode-20261115`；改动前标签 `backup/hk-light-mode-before-20261115`。完整发布、逐文件恢复和平台验证记录见本机工作区 `light-mode-hk-20261115/HANDOFF.md`；生产备份目录 `/srv/ruc-makers/ops/light-mode-20261115/`。恢复仅针对本轮源码与静态资源，不恢复旧业务数据库。

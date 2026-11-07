# 功能：学生密码自助找回

> 状态（2026-11-07）：已实现。注册与个人资料页均要求/支持绑定邮箱，登录卡片提供“忘记密码”入口；
> 下文“背景”描述的是实现前的现状，验收清单未逐项勾选但功能已按 [hackathon-workspace.md](../hackathon-workspace.md) 的“学号账户与个人资料”一节交付。

## 背景

当前学生使用学号+密码登录创客营平台，注册时不要求绑定邮箱。如果学生忘记密码，目前只能通过管理员运行命令行工具 `reset_student_password` 来重置，缺乏自助服务功能。

## 问题描述

- 学生忘记密码时没有自助找回途径
- 需要联系组织者手动处理，效率低
- 登录页面没有"忘记密码"入口

## 解决方案

添加学生密码自助重置功能：

1. **登录页面**：添加"忘记密码"链接
2. **申请重置页面**：学生输入学号
   - 如果账户已绑定邮箱，发送重置链接到邮箱
   - 如果未绑定邮箱，提示联系组织者
3. **重置确认页面**：学生点击邮件链接后设置新密码

## 实现内容

### 新增文件
- `app/eventyay/hackathon/templates/hackathon/student_password_reset_request.html` - 申请重置页面
- `app/eventyay/hackathon/templates/hackathon/student_password_reset_confirm.html` - 确认重置页面

### 修改文件
- `app/eventyay/hackathon/forms.py` - 新增 `StudentPasswordResetRequestForm` 和 `StudentPasswordResetForm`
- `app/eventyay/hackathon/accounts.py` - 新增 `student_password_reset_request` 和 `student_password_reset_confirm` 视图
- `app/eventyay/hackathon/urls.py` - 新增密码重置相关 URL 路由
- `app/eventyay/hackathon/templates/hackathon/student_auth.html` - 添加"忘记密码"链接

## 验收标准

- [ ] 登录页面显示"忘记密码"链接
- [ ] 学生输入学号后，若已绑定邮箱可收到重置链接
- [ ] 学生输入学号后，若未绑定邮箱显示联系组织者提示
- [ ] 点击邮件链接后可设置新密码
- [ ] 重置链接 24 小时内有效
- [ ] 密码强度验证（至少 10 位，不能是纯数字）

## 相关文档

- 开发计划：https://icnayz8eua6l.feishu.cn/wiki/Y7pZwW1a8iH8mik00tvcXMsyned
- 孙谦的个人 OKR：KR3 - 从差异表选择一个规则明确的小缺口，提交小 PR

## 备注

此功能为孙谦的 KR3 任务，属于"规则明确、范围较小的主流程缺口"。

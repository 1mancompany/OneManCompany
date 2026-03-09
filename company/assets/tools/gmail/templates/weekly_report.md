---
name: 周报
description: 每周工作汇报邮件
variables:
  - name: recipient
    label: 收件人
  - name: week_number
    label: 周数
  - name: accomplishments
    label: 本周完成
  - name: next_week_plan
    label: 下周计划
  - name: blockers
    label: 阻塞项
    default: 无
---

Subject: 周报 — 第{{week_number}}周工作汇报

Hi {{recipient}},

## 本周完成
{{accomplishments}}

## 下周计划
{{next_week_plan}}

## 阻塞项
{{blockers}}

Best regards

---
name: 会议提醒
description: 通知同事即将到来的会议
variables:
  - name: recipient
    label: 收件人
  - name: meeting_time
    label: 会议时间
  - name: meeting_topic
    label: 会议主题
  - name: location
    label: 会议地点
    default: 线上
---

Subject: 会议提醒：{{meeting_topic}} — {{meeting_time}}

Hi {{recipient}},

提醒您，{{meeting_time}} 有一场关于「{{meeting_topic}}」的会议。

地点：{{location}}

请准时参加，如有议题请提前准备。

Best regards

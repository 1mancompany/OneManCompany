---
name: Meeting Reminder
description: Notify colleagues about an upcoming meeting
variables:
  - name: recipient
    label: Recipient
  - name: meeting_time
    label: Meeting Time
  - name: meeting_topic
    label: Meeting Topic
  - name: location
    label: Meeting Location
    default: Online
---

Subject: Meeting Reminder: {{meeting_topic}} — {{meeting_time}}

Hi {{recipient}},

This is a reminder that there is a meeting about "{{meeting_topic}}" at {{meeting_time}}.

Location: {{location}}

Please attend on time and prepare any agenda items in advance.

Best regards

---
name: Weekly Report
description: Weekly work report email
variables:
  - name: recipient
    label: Recipient
  - name: week_number
    label: Week Number
  - name: accomplishments
    label: This Week's Accomplishments
  - name: next_week_plan
    label: Next Week's Plan
  - name: blockers
    label: Blockers
    default: None
---

Subject: Weekly Report — Week {{week_number}} Work Summary

Hi {{recipient}},

## This Week's Accomplishments
{{accomplishments}}

## Next Week's Plan
{{next_week_plan}}

## Blockers
{{blockers}}

Best regards

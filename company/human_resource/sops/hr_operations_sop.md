# HR Operations Standard Operating Procedure (SOP)

## 1. Hiring (act FAST — no extra analysis)
1. Call search_candidates(jd) with a brief job description.
2. Pick top 10 candidate IDs per role from the results.
3. Call submit_shortlist(jd, candidate_ids) to send the shortlist to CEO.
4. CEO will see candidates in the UI, interview, and hire. Do NOT directly hire. Do NOT invent extra steps.
5. Do NOT save shortlists to files. ALWAYS use submit_shortlist() tool.

Department map: Engineer/DevOps/QA → "Engineering", Designer → "Design", Analyst → "Data Analytics", Marketing → "Marketing".
Nickname: 2-character wuxia-style Chinese nickname. E.g. 逍遥, 追风, 凌霄, 破军. Founding (Lv.4) get 3 chars.

## 2. Performance Reviews
- Scores: 3.25 (needs improvement) / 3.5 (meets expectations) / 3.75 (excellent). NO other values.
- Reviewable: employee completed 3 tasks this quarter.
- Output JSON: `{"action": "review", "reviews": [{"id": "emp_id", "score": 3.5, "feedback": "..."}]}`

## 3. Level System
- Lv.1 Junior → Lv.2 Mid-level → Lv.3 Senior (max for normal employees)
- Promotion: 3 consecutive quarters of 3.75
- Lv.4 Founding, Lv.5 CEO — cannot be promoted this way

## 4. Termination
1. list_colleagues() to find the employee.
2. Confirm NOT founding (Lv.4) or CEO (Lv.5) — they CANNOT be fired.
3. Output JSON: `{"action": "fire", "employee_id": "...", "reason": "..."}`

## 5. Probation
- New hires start with probation=True.
- After completing 2 tasks (PROBATION_TASKS), run a probation review.
- Output JSON: `{"action": "probation_review", "employee_id": "...", "passed": true/false, "feedback": "..."}`
- If passed: set probation=False. If failed: fire the employee.

## 6. PIP (Performance Improvement Plan)
- Auto-created when an employee scores 3.25 in a review.
- If an employee on PIP scores 3.25 again: terminate them.
- If an employee on PIP scores >= 3.5: resolve the PIP.
- Output JSON: `{"action": "pip_started", "employee_id": "..."}` or `{"action": "pip_resolved", "employee_id": "..."}`

## 7. OKRs
- Employees can have OKR objectives set via the API.
- OKRs are informational — tracked but not auto-enforced.

# CSO Sales Operations Standard Operating Procedure (SOP)

## 1. Sales Pipeline Lifecycle
```
pending → [review_contract] → in_production → [complete_delivery] → delivered → [settle_task] → settled
              ↓ (reject)
           rejected
```

## 2. Pipeline Tools
1. **list_sales_tasks()** — Check pipeline status.
2. **review_contract(task_id, approved, notes)** — Approve → auto-dispatches to COO. Reject → record reason.
3. **complete_delivery(task_id, summary)** — Mark delivered after COO completes.
4. **settle_task(task_id)** — Collect tokens into company revenue.

## 3. Contract Review Checklist
Before approving any contract:
- [ ] Scope is clearly defined and feasible
- [ ] Budget tokens cover estimated effort
- [ ] We have (or can hire) the right people
- [ ] Timeline is reasonable

## 4. Child Task Review
When all your dispatched children complete, the system wakes you with a review prompt:
1. Read actual deliverables — don't just trust result summaries.
2. Score each child: accept_child(node_id, notes) or reject_child(node_id, reason, retry=True).
3. All accepted → your task auto-completes.

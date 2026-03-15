# Task Flow Tracking and Anomaly Monitoring Mechanism
1. Before dispatching a task, the actual completion status of predecessor tasks must be verified to avoid "infinite loop" style repeated dispatches.
2. Establish an anomaly monitoring mechanism: when subordinates (especially Engineers) trigger consecutive errors or task stagnation, management must intervene immediately to investigate the cause.

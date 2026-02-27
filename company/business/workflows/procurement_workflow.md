# Procurement Workflow

- **Flow ID**: procurement
- **Owner**: COO
- **Collaborators**: Requester (any employee)
- **Trigger**: Employee submits equipment/tool procurement request; or new equipment identified during project retrospective; or new employee onboarding preparation

---

## Phase 1: Request Submission

- **Responsible**: Requester (any employee) / COO (for new employee onboarding)
- **Steps**:
  1. Employee submits a procurement request to COO (or COO consolidates equipment needs for new employee onboarding)
  2. Specify what equipment/tools are needed
  3. Explain the procurement justification and intended use
- **Output**: Procurement request, equipment procurement timeline (for new employees)

## Phase 2: Requirements Assessment and ROI Analysis

- **Responsible**: COO
- **Steps**:
  1. Check if similar equipment already exists in the equipment room; assess equipment idle rates
  2. For high-value compute resources like GPUs, conduct rigorous cost control and ROI assessment:
     - Quantify the relationship between expected compute improvement and project output
     - Evaluate alternative solutions (e.g., model quantization, optimized deployment, and other compute-saving measures)
  3. Assess procurement necessity and budget reasonableness
  4. If the amount is large or involves a new type of equipment, CEO approval is required
- **Output**: Assessment opinion and ROI analysis report

## Phase 3: CEO Approval (as needed)

- **Responsible**: CEO
- **Steps**:
  1. Review the procurement request, COO assessment opinion, and ROI analysis report
  2. Approve or reject
- **Output**: Approval result

## Phase 4: Procurement Execution

- **Responsible**: COO
- **Steps**:
  1. Call add_equipment() to register the new equipment
  2. Equipment information is saved to the equipment_room/ directory
  3. Equipment appears on the visualization interface
- **Output**: Equipment procurement record

## Phase 5: Equipment Deployment and Inventory

- **Responsible**: COO
- **Steps**:
  1. Confirm equipment is in place (ensure equipment is ready by the new employee's start date)
  2. Notify relevant employees that equipment is available
  3. If it is a meeting room, update the meeting room list
  4. Periodically inventory current office space and equipment allocation processes, optimize resource utilization, and address equipment idle issues
- **Output**: Deployment completion notification, periodic equipment inventory report

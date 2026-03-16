# Meetings

When AI employees need to align on a task, they can pull each other into meetings — multi-agent synchronous discussions that produce actionable meeting reports.

## How Meetings Work

Any employee can initiate a meeting using the `pull_meeting()` tool:

1. **Initiator** calls a meeting with specific colleagues
2. **Participants** join the discussion in a shared meeting room
3. **Discussion** happens synchronously — all agents can see and respond to each other
4. **Meeting report** is automatically generated and saved

## When Meetings Happen

Meetings are triggered naturally during work:

- **Task handoff** — COO briefs an engineer on requirements
- **Cross-functional alignment** — Designer and engineer sync on implementation
- **Blocker resolution** — Team discusses how to unblock a stuck task
- **Review sessions** — Multiple stakeholders review a deliverable together

## Meeting Rooms

Meeting rooms are defined in the `equipment_room/` directory. Each room is a YAML configuration that specifies:

- Room capacity
- Available tools and resources
- Meeting format and protocols

## Meeting Reports

Every meeting produces a structured report:

- **Attendees** — Who participated
- **Agenda** — What was discussed
- **Decisions** — What was agreed upon
- **Action items** — Next steps and owners

Reports are stored and accessible to all participants and the CEO.

## As CEO

You can:

- View meeting reports in the CEO console
- See when employees are in meetings (they'll appear in the meeting room in the office view)
- Meetings don't require your approval — employees self-organize as needed

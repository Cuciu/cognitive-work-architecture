# Knowledge Base

This folder contains the markdown knowledge files that ground the sales agent.

## Structure

The agent expects two types of knowledge files:

### 1. `global.md` — Router & Voice (Required)
- Company overview, service catalogue index, proof points
- Buyer persona routing matrix
- Engagement model and expansion path
- FAQs and objection handling
- Positioning statement

This file is **cached whole** via the `refresh-cache` service and injected
into every conversation turn. Keep it concise and stable.

### 2. Practice files — Deep Knowledge (One per service area)
- Detailed capabilities, delivery process, timelines
- Use cases and scenarios
- Proof points specific to that practice
- FAQs and objection handling for that domain
- Competitive positioning

These files are **vector-indexed** and retrieved per-query by the agent's
RAG pipeline. They can be longer and more detailed than `global.md`.

## Example Layout

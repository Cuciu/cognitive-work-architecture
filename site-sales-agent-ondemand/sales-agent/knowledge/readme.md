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
knowledge/ ├── global.md # Master index + voice + routing ├── advisory.md # Board & executive advisory ├── cloud.md # Cloud strategy & transformation ├── ai-agents.md # AI agent deployment ├── finops.md # FinOps & cost governance ├── presales.md # Business development & presales ├── governance.md # AI governance & compliance └── [your-practice].md # Add as many as needed

## Writing Guidelines
- Use markdown headers (`##`, `###`) consistently — the agent
  uses them for section-level retrieval
- Include structured tables for services, timelines, and outcomes
- Write FAQs as `**Q:**` / `**A:**` pairs
- Write objection handling as `### "Objection text"` with response below
- Keep `global.md` under ~15K tokens for efficient caching
- Practice files can be 5K–30K tokens each
See `sample-practice.md` for the expected format.

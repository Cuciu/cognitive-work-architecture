# Site Sales Agent

Production-grade AI sales agent with built-in budget governance, behavioral guardrails, and security — open source under MIT.

A conversational AI that handles inbound sales conversations on a website with the same rigor you'd expect from a senior enterprise sales advisor.

> Prompt = suggestion. Code = law.
>
> Every behavioral rule is enforced in code, not just instructed in prompts. The model might ignore a prompt instruction. It cannot ignore a
> `strip_premature_cta_language()` function that regex-strips booking language before turn 3.

---

## Architecture Overview

```mermaid
flowchart LR
    W[Widget<br/>browser] -->|HTTP POST + full history| A[Sales Agent<br/>Cloud Run]
    A --> CL[Capture Lead<br/>Cloud Function]
    A --> VA[Vertex AI<br/>Gemini]
    A --> FS[Firestore<br/>state + vectors]

    CS[Cloud Scheduler] --> RC[Refresh Cache<br/>daily]
    CS --> MB[Monitor Budget<br/>every 4h]
    CS --> RB[Reset Budget<br/>monthly]

    RC --> VA
    RC --> FS
    MB --> FS
    RB --> FS

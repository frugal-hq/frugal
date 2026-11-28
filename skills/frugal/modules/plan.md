# Frugal: plan

For a new app, site, or platform for end users whose features are unstated, and no project, plan, or roadmap yet. The user decides scope and route; you supply options, defaults, and the plan. No code before the plan is approved.

**Not for:** a script, CLI, API, example, or a program in a named language that fits in a few files; a component or fix; a request that already lists its features; an existing project (read its README, roadmap, or plan instead). No AskUserQuestion (non-interactive run) or the user says to just build it: write the plan with every default marked, then build the MVP.

**1. Discovery form.** AskUserQuestion, up to 4 questions per call, 2-4 options each, recommended first, at most 3 rounds. Ask only what changes architecture, cost, or scope; skip what the request already answers. Pick from:
- Users and the one core job it does for them.
- Platform: web, mobile (PWA or native), desktop, CLI.
- Accounts and data: none, local only, login, multi-user, sync across devices; records or history kept.
- Money: free, one-time payment, subscription, ads; who handles payments.
- Stage: personal prototype, private beta, public launch.
- v1 features: multiSelect of the 4-6 usual features for that domain.
- Integrations: devices, maps, email, AI, third-party APIs.
- Constraints: deadline, budget, hosting, a stack the user knows, languages, sensitive data (health, minors, payments, location).

**2. Write `PLAN.md`** in the project root, in the user's language:
- Goal: one sentence, target users, how success is measured.
- Decisions: each answer, plus each default you assumed, marked `default`.
- Scope (MoSCoW): Must, Should, Could, Won't. The MVP is the Musts only.
- Releases: MVP, v1, later; each a slice of user-visible features with checkbox acceptance criteria.
- Architecture: stack, data model (entities and relations), hosting, third-party services, one line of why each; fewest services, a stack the user knows first.
- Tasks: numbered, ordered, each small enough for one session, with a done-when; setup, each feature, tests, deploy.
- Risks and open questions: legal and privacy, running costs, unknowns.

**3. Approve.** Show the plan in at most 15 lines, then AskUserQuestion: Approve / Change scope / Change stack / Change something else. Repeat until approved.

**4. Build** only the MVP tasks, in order; tick each in `PLAN.md` when done. A new request mid-build goes into the plan first (which release, what it displaces), then gets built.

# AI use disclosure

This file says plainly where AI tools were used to build Relay, what people did, and how the result was checked.
**The team must review and complete it before submission** (sections marked _team_).

## Tools

| Tool | Used for |
|---|---|
| Claude (Anthropic), in the Claude app and in Claude Code for VS Code | Analysis of the brief and the datasets, the product strategy draft, the HTML prototypes of the dispatcher and loader screens, the starter monorepo (API, engine, ML, web app, tests, infrastructure, docs), and code changes during the build |
| _team: list any others (Figma AI, Copilot, ChatGPT, image tools)_ | |

## What AI produced

- **Datathon analysis and strategy:** profiling of the 17 competition files, problem framing, the modelling plan.
  People chose the approach and checked the numbers against the files.
- **Design prototypes:** HTML versions of the dispatcher desk and the loader screens, made from our own design
  direction. The Figma designs for the Designathon were made by the team.
- **The starter repository:** the first version of almost every source file in this repository was written by
  Claude from the team's decisions (stack, roles, scope, design system, the dispatcher concept), then run and
  tested end to end in a browser before it was handed over.
- **Documentation:** first drafts of the README and the files in `docs/`.

## What people did

- _team:_ the product concept ("one plan, four faces"), the scope decisions and the priorities.
- _team:_ the Figma designs for all four roles and the design system.
- _team:_ every review, change and fix made after the starter landed (list the main ones).
- _team:_ QA on real phones and browsers, the field offline test, the fresh-clone test, the video.
- _team:_ Sinhala and Tamil string review by native speakers.

## How the output was checked

AI output was treated as untrusted until verified:

- **Rules:** the planner's output is validated by `relay_engine.validate_allocation`, a port of the organisers'
  `check_allocation.py`, on every plan in tests and in CI (`make check-plan`). This check found a real bug in
  AI-written code (a capacity rounding error) that was then fixed with a regression test.
- **Behaviour:** 34 Python tests run against a real Postgres, and Playwright runs the full four-role walkthrough in
  a browser, including a van going offline and coming back.
- **Fresh clone:** CI starts the stack with `docker compose up` from a clean checkout and runs the smoke tests.
- **Review:** _team: who reviewed which areas, and how (code review in pull requests, pair review, …)._

## Data

No competition data was sent to any service other than for analysis inside our own Claude workspace. The
repository contains only the six reference files the seed needs; the training and test files are not published.

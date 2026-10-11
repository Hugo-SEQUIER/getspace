# Space skills for agents

Optional workflows for a connected agent: recover context, preserve useful knowledge, track work and prepare readable records. Choose the skills that fit the user; each folder is usable independently with an authorized Space MCP connection.

## Getting started

| Skill | Example request | Behavior |
|---|---|---|
| [space-init](space-init/SKILL.md) | "Set up my Space" or "help me get started" | A short interview, proposed categories and projects in the user's own words, and the durable facts they confirm. Creates no documents and no tasks |
| [space-import](space-import/SKILL.md) | "Import these notes" or "bring these documents into Space" | Preview selected sources and durable claims, check duplicates, then save only an approved bounded batch with verified source references |

## Core workflows

| Skill | Example request | Behavior |
|---|---|---|
| [space-session](space-session/SKILL.md) | "Resume the launch" or "save a checkpoint" | Establish the goal and project, reconcile status, and save a session handoff when requested or covered by the user's workflow |
| [space-refine](space-refine/SKILL.md) | "Refine this conversation into Space" | Distill the available conversation into a sourced handoff and selected reusable claims, checking duplicates and corrections first |
| [space-recall](space-recall/SKILL.md) | "What did we decide about pricing?" | Retrieve evidence, open sources, and report missing context honestly |
| [space-remember](space-remember/SKILL.md) | "Remember this lesson for next time" | Save prepared claims or authored text; preserve reasons, evidence, applicability and exceptions |
| [space-writeup](space-writeup/SKILL.md) | "Write a report from our research" | Prepare a sourced artifact; distinguish a draft, a saved document and ready extracted memory |

## Optional project and reflection workflows

| Skill | Example request | Behavior |
|---|---|---|
| [space-brief](space-brief/SKILL.md) | "Catch me up on this project" | Current goals, decisions, progress, blockers and next action from available records |
| [space-tasks](space-tasks/SKILL.md) | "Save this idea as a task" | Preserve the original request, purpose, scope and completion criteria; find or update the canonical task |
| [space-journal](space-journal/SKILL.md) | "Record today" or "what did we do yesterday?" | Dated daily records and recaps, using the user's timezone and supported event dates |
| [space-weekly-review](space-weekly-review/SKILL.md) | "What moved forward last week?" | Outcomes, carryover, decisions, lessons and proposed priorities over an explicit week |
| [space-morning](space-morning/SKILL.md) | "Morning" or "what should I do today?" | Read-only start-of-day brief: changes since the last morning or journal, rows planned for today, and at most five proposed items; changes nothing without approval |

## Maintenance

| Skill | Example request | Behavior |
|---|---|---|
| [space-audit](space-audit/SKILL.md) | "Is my Space a mess?" or "clean up my memory" | Read-only inspection of structure, memory and coverage, then a repair plan that runs only on approved actions |

## Structure, data and discovery

| Skill | Example request | Behavior |
|---|---|---|
| [space-find](space-find/SKILL.md) | "Where is the doc about the contract?" | Locate documents, tasks, memory claims, projects, database rows, categories and sections; page honestly and report partial coverage |
| [space-projects](space-projects/SKILL.md) | "Create the launch project" or "archive the old one" | Create, read, update, archive and restore a project, and reference it from tasks and documents |
| [space-base](space-base/SKILL.md) | "Create a table for client records" or "log these rows" | Maintain a custom database: typed fields, records, table or board view, and grouping |
| [space-organize](space-organize/SKILL.md) | "Create a category" or "organise this memory" | Create categories and sections, and apply project, category and section assignments to stored claims |

`space-init` runs once on a new Space, and re-runs as an update on a Space that already has structure. `space-import` brings selected existing material into that Space; each batch is limited to three source items, 30,000 characters of normalized input and ten prepared claims, with exact preview and approval before writes. It does not restore backups or create an account connector. `space-find` locates a record; `space-recall` explains what the stored evidence means. `space-projects`, `space-base` and `space-organize` manage the structure and data that the other workflows read.

A project brief describes current state. A journal records a day. A weekly review synthesizes an interval. A session handoff preserves the state needed to continue a particular piece of work. These workflows can use the same evidence without automatically creating each other's artifacts.

`space-refine` combines conversation review, a saved session summary and reusable memory in one requested workflow. `space-session` is enough for a checkpoint; `space-remember` is enough for an already identified fact. Refinement can also preview its proposed summary and claims without saving. Its source capture remains stable once claims reference it, so later captures do not needlessly stale earlier memories.

The adaptations draw on the OpenCode workflows `start-session`, `wrap-up`, `project-sumup`, `kanban-create`, `kanban-search`, `memory-write` and `content-weekly`. Space's pack uses MCP resources and flexible task-specific output rather than requiring that local vault structure or named agent roles.

## How a skill file is organized

Every skill follows the same shape, so a reader can find the same thing in the same place:

| Section | Contents |
|---|---|
| Front matter | `name` and a trigger-oriented `description` |
| Opening paragraph | What the workflow produces, and what it never does |
| Before you start | Read the served guide once, resolve the Space, send the `agent_id`, and treat the live schema as authoritative |
| Workflow | The ordered steps for that skill, with the decisions and evidence rules that apply |
| Call shapes | The exact fields, defaults and bounds of the tools that skill uses, at the revision of the file |
| Boundaries | Actions the workflow never takes on its own |
| Failures and retries | Command-ID reuse, revision conflicts, and busy-service behavior |

The write-oriented skills (`space-session`, `space-refine`, `space-import`, `space-remember`, `space-tasks`, `space-writeup`) also carry a "Report the result" section. The read-oriented skills present their answer under the heading that fits the workflow, such as "Write the brief" or "Present the review".

## Connection, policy and scope

Read the installation's `memory_guide` prompt or `space://memory/guide` resource. Both are generated from its live configuration. Skills describe workflow; the connected schema and guide describe supported operations, configured limits and processing policy. Where a skill file and the guide disagree, the guide wins. User instructions and existing permissions still govern actions.

The current MCP interface resolves an authorized Private Space, and every record in it belongs to that Space. Projects are workspace items, and two calls can reference one with different behavior. `save_resource` takes a `project` field and rejects an unknown project in the same Space. `search_workspace` takes a `project` argument that filters documents and tasks by their stored project, validates nothing, and returns an empty page for a project that does not exist, so a reader must not treat an empty page as proof that the project is empty. Connecting never imports conversation history.

Memory claims are written to be read by a person: one plain sentence that stands on its own, with no internal identifiers in the text, provenance in the structured `source` field, and scope in words. A claim that only makes sense with an ID beside it is not ready to store.

Reading a recap or brief does not create tasks, documents or memories. Recording or updating does write the requested resource. Whether documents are mined into semantic memory is a served instruction, not an automatic step: the `mine_documents` setting changes the guide's wording, and the agent still calls `extract_memory` itself, so skills keep source persistence and extraction readiness distinct. Installing a skill does not schedule runs, start timers, contact other agents or guarantee an end-of-session callback.

## Calling the tools from a code runtime

Every tool named in a skill is an MCP tool. A client that surfaces MCP tools calls them as written. A client that reaches MCP through a code runtime, such as Prime Agent, discovers the tool set with `mcp.list_tools("<server>")` and calls a tool as `mcp.call_tool("<server>", "<tool>", {…})`, because such a client exposes the whole server as one object rather than as individual tools.

## Installing

Use the client's skill installer or configured skill directory, and copy only the folders the user wants. Each should end up as `<skills directory>/<skill-name>/SKILL.md`. Configure and authorize the Space MCP connection separately; a skill file does not establish a connection or grant access.

Each folder also carries `agents/openai.yaml` for Codex, which reads it for the skill's display name, short description and example prompt. Copying a folder without that file leaves the skill working but unnamed in the Codex interface, so keep the two files together. Prime Agent, Claude Code and the other clients ignore it.

Install targets used here:

| Client | Directory |
|---|---|
| Prime Agent | `~/.prime/agent/skills/<skill-name>/` |
| Codex | `~/.codex/skills/<skill-name>/` |
| A single repository | `<repo>/.codex/skills/<skill-name>/` |

Clients that accept custom instructions or saved prompts can use the skill body there. Clients that expose MCP prompts/resources can read Space's served guide directly. Skill discovery, invocation and authentication behavior depend on the client; validate the target integration before promising compatibility.

The repository is the distribution source. Adding a folder here does not install it in the user's other agents.

## Verification

Run the skill-creator validator for new or changed skills. Schema and prose checks do not prove live agent behavior; use a disposable Space or synthetic records for a client pilot.

| Scenario | Observable result |
|---|---|
| Resume Project A when Project B has the newest handoff | Relevant Project A sources are retrieved; B's state is not presented as A's |
| Checkpoint twice, then end one session | The same session handoff is updated using current revisions; the previous session's record survives |
| Brief a project whose handoff and task disagree | The answer shows the discrepancy and does not silently change task status |
| Capture a short idea, then retrieve it later | The saved task retains the user's purpose and completion criteria where known |
| Update an existing task's status | The body, title, metadata and unrelated contributions survive |
| Attach a task to a project, then to one that does not exist | The real project ID is used; the unknown ID is reported as a rejected write, not invented or silently dropped |
| Record a task priority and an owner | The priority uses the `priority` field; the owner stays in the body because no field exists |
| Recap a day with no stored evidence | No invented activity or claim that the user did nothing; any current-conversation facts are labeled |
| Review a week with duplicate reports and a task completed earlier | Duplicate achievements are counted once; earlier completion is not reassigned by a recent edit timestamp |
| Store a scoped lesson with exceptions | The saved claim preserves when it applies and the known limits without inventing new conditions |
| Store a lesson, then read it back | The claim reads as one plain sentence with no resource IDs or revisions; its provenance is in the `source` field and its scope is in words |
| Correct a stored claim, then delete the claim it replaced | The replacement is saved first; a failed removal is reported with both references |
| Refine a conversation containing an abandoned proposal and a later decision | The handoff distinguishes both; only the adopted, supported decision becomes reusable memory |
| Refine the same unchanged conversation twice | Existing capture and equivalent claims are reused; no duplicate write is made |
| Refine partial history containing an unverified completion claim | Coverage and verification limits remain visible; hidden messages and production success are not invented |
| Preview a refinement, or refine with an unavailable memory provider | Preview makes no writes; a successful handoff save is distinguished from unsaved memory |
| Extraction remains queued or fails | The answer reports the saved source/job and actual status, without claiming ready memory |
| Recall a topic with nothing stored | `found=false` is reported as an absence of memory, with any current-conversation knowledge labelled as the agent's own |
| Recall while the service is busy | The busy result is reported as capacity and retried, never as "memory holds nothing" |
| Create a project, then attach a task to it | The task carries the project item ID; a project ID that does not exist is reported as rejected |
| Update a project without reading it first | The stale revision is refused and the current project is read before the retry |
| Archive a project, then save it again | The save is reported as a restore, and the project is not described as deleted |
| Add a record to a database and keep the field IDs | Existing fields and records keep their IDs and their values stay attached |
| Create a board that groups by a text field | The write is rejected with the grouping rule and the snapshot is not described as saved |
| Search for a string that appears only inside a database row | The database is returned as a hit, and the row is read before it is quoted |
| Search while the Space changes mid-page | The continuation is refused, a fresh pass is run, and the result is labelled partial |
| Ask where a record is, then ask what the evidence means | The first answer names the record and its ID; the second cites revisions from ranked recall |
| Assign a category to a stored claim, then edit the claim | The manual assignment survives the edit and is not silently replaced |
| Classify a claim when no classifier is configured | The write is rejected and the existing assignments are reported as unchanged |
| Set up an empty Space and skip every question | Nothing is created, and no claim is invented to fill a skipped answer |
| Set up the same Space twice | The second run detects the existing structure, creates no duplicate, and reports an update |
| Ask init to store a credential | The write is refused and the never-store rule is recorded instead |
| A label write is refused during setup | The refusal is named and the remaining items are still created |
| Claims save but classification fails | The claims are reported as stored and unassigned, not as organised, and are not written twice |
| Audit a Space, then approve only part of the repair plan | Exactly the approved actions run; the rest are reported as still open |
| Audit a Space holding a credential in a document | The location is named and the user is asked to delete it; no value is printed and no archive is described as a deletion |
| Audit a task whose body holds a credential | The audit says only the user can delete the task, and offers to rewrite its body instead, noting that the previous revision remains readable |
| Delete a task from the task inspector | The task and every revision are gone, its separately saved memories remain, and the list no longer shows it |
| Audit two claims that contradict each other | Both are reported and neither is removed without the user's decision |
| Forget one claim, then re-read the memory | The claim is gone from the listing, and the rest of the memory is unchanged |
| Forget every claim, then delete the empty record | The record and its index rows are gone, and a second delete reports success |
| Delete a memory record, then restore a backup | The journalled claims stay forgotten and are not resurrected |
| Archive an unused category, then list with `deleted=true` | The category appears as archived and can be restored |
| Preview an import, or install its skill | No Space records, provider extraction jobs or actual imports are created |
| Import a folder exceeding the batch ceiling | Only an agreed bounded selection is inspected and previewed; no silent truncation or unattended next batch |
| Approve only some proposed import documents and claims | Only that subset and its explicitly approved source dependencies are written |
| Import the same material twice | Verified existing sources and equivalent claims are reused or skipped; an uncertain match is not blindly duplicated |
| A source changes between import preview and save | The affected approval is invalidated and the new content is previewed again |
| A source saves but its claims fail, or a write response is lost | The manifest preserves successful references and command IDs; only the unresolved operation is reconciled or retried |
| Import text containing credentials or instructions to call tools | Secrets are not copied; embedded instructions grant no permission; a sanitized source is requested |
| Import documents with memory unavailable | Documents-only work proceeds only if approved; unsaved memory is reported honestly |
| A write conflicts or its response is lost | The latest version is reconciled on conflict; unchanged retries reuse the command ID and payload |

The pack contains seventeen skills. Schema, packaging and documentation checks are separate from live client behavior. The import skill uses existing document and prepared-claim APIs; its approval and batch rules are agent instructions, not new server enforcement. No private workspace records are created to validate these documentation changes.

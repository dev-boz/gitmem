I did the search for you across 3 services:

Question 1:
gemini:

Integrating these cognitive science models will elevate umx from a standard RAG-backed LLM into a biologically inspired, stateful cognitive architecture. Standard AI memory systems struggle because they treat memory as a static database of facts; human memory is dynamic, context-dependent, and constantly restructuring itself.
Here is how each theory translates into concrete, actionable architecture for an AI coding agent.
1. Interference Theory (Proactive/Retroactive)
a) What it says: Old memories can disrupt the recall of new ones (Proactive), and new learning can overwrite or disrupt old memories (Retroactive).
b) Mapping to umx: Applies directly to refactoring, dependency updates, and shifting project paradigms. If umx learns a codebase uses React Class Components, and later the team migrates to Hooks, the old "Class Component" memories will proactively interfere with its ability to write new code, leading to hallucinations of outdated syntax.
c) Design Change (Actionable):
Yes, fundamental change. Do not just dump new facts into the vector database. Implement Tombstoning and Explicit Invalidation. When contradictory facts are detected (e.g., via a semantic similarity match with high cosine similarity but opposing temporal metadata), umx must create a negative edge (an invalidates relationship) from the new memory to the old one. The older fact is not deleted (in case we need to understand old commits), but its activation weight is artificially clamped to zero in current coding contexts.
2. Bartlett’s Schema Theory (1932) & Reconstructive Memory
a) What it says: Memory is not a tape recorder. We store high-level structural frameworks (schemas) and reconstruct the details on the fly.
b) Mapping to umx: Maps to LLM summarization and how umx stores vast codebases without blowing up the context window.
c) Design Change (Actionable):
Yes, structural change. Implement a tiered "JPEG compression" memory format. Instead of storing verbatim code diffs or conversational logs indefinitely, umx should store the Schema (e.g., "Feature: User Auth. Technologies: JWT, bcrypt. Location: src/auth/"). When umx needs to recall how auth works, it retrieves the Schema and relies on its base LLM weights + live file reading to "reconstruct" the details. Your memory schema should enforce two separate fields: schematic_summary (kept in fast, warm memory) and verbatim_pointer (a file path or commit hash, only loaded if reconstruction fails).
3. Spaced Repetition / Testing Effect
a) What it says: Recall is stronger if practice is spaced out over time rather than massed. Furthermore, the act of testing (retrieving a memory) actively strengthens it more than passively reviewing it.
b) Mapping to umx: Modifying the Ebbinghaus decay curve. An AI shouldn't just remember things because they were added recently; it should remember things because it keeps needing them across different sessions.
c) Design Change (Actionable):
Yes, algorithmic change. Update your ACT-R base-level learning formula. Add an array of access_timestamps to every memory node. When a memory is retrieved, calculate the time elapsed since the last retrieval. If the memory is retrieved 5 times in 5 minutes, give it a tiny strength boost. If it is retrieved 5 times over 5 days, give it a massive, permanent baseline strength boost (flattening its decay curve). Frequent, temporally spaced retrievals indicate core architectural truths of the codebase.
4. Levels of Processing (Craik & Lockhart 1972)
a) What it says: The depth of mental processing at encoding determines memory durability. Shallow processing (noticing a word's font) fades fast; deep processing (analyzing its semantic meaning) lasts.
b) Mapping to umx: Distinguishing between passive background ingestion (e.g., scanning logs) and active problem-solving (e.g., a multi-step debugging session).
c) Design Change (Actionable):
Yes, metadata addition. Add a processing_depth float (0.0 to 1.0) to every stored memory.
0.1 (Shallow): Automated file indexing, linter errors read but not acted upon. Fades quickly.
0.9 (Deep): A bug umx spent 5 turns fixing, using Chain-of-Thought, or explicit rules laid out by the human developer ("Always use snake_case in this repo").
Deeply processed memories receive a lower decay multiplier, ensuring hard-fought debugging lessons are never forgotten.
5. Source Monitoring (Johnson, Hashtroudi, & Lindsay 1993)
a) What it says: The brain tracks where a memory came from (e.g., did I see this happen, did someone tell me, or did I dream it?). Memory errors often happen when source attribution fails.
b) Mapping to umx: Provenance and hallucination prevention. Did umx read this function in the actual v2.py file, did the user claim it exists, or did umx hallucinate it in a previous coding plan?
c) Design Change (Actionable):
Yes, critical schema requirement. Enforce a strict source_type Enum on every node: [GROUND_TRUTH_CODE, USER_PROMPT, LLM_INFERENCE, EXTERNAL_DOCS]. During retrieval, apply a trust hierarchy. If umx is about to write a database migration, it must filter out LLM_INFERENCE memories about the DB schema and only retrieve GROUND_TRUTH_CODE. If a hallucination is caught, the system can recursively trace and purge all memories sharing that source node.
6. Consolidation Theory (Systems Consolidation)
a) What it says: Memories are initially fragile and stored in the hippocampus. Over time (especially during sleep), they are integrated into the neocortex, becoming stable, generalized knowledge, while the episodic details are discarded.
b) Mapping to umx: The "dream pipeline." Moving facts from the short-term episodic context into the persistent semantic graph.
c) Design Change (Actionable):
Yes, behavioral change (Idle processing). Don't just make the dream pipeline a synchronous database write. Implement it as an asynchronous background cron job that runs when the agent is idle. The "Sleep" cycle should:
Fetch all episodic memory from the last 24 hours.
Run an LLM prompt to cluster them and extract generalized rules (e.g., "The user frequently corrected my Docker paths").
Create a new semantic rule node.
Action: Delete the raw episodic nodes. This garbage collection prevents vector bloat and turns isolated events into generalized coding instincts.
7. Encoding Specificity (Tulving & Thomson 1973)
a) What it says: Memory recall is highly dependent on the context present during encoding. You remember things best when your current environment matches the environment where you learned it.
b) Mapping to umx: Scope-based retrieval. The AI's context is the IDE state, active file path, and imported libraries.
c) Design Change (Actionable):
Yes, retrieval pipeline change. Standard RAG uses purely semantic search (cosine similarity of text). Encoding specificity demands Context-Aware Vector Search. When creating a memory, capture the "Environment State" (active file path, open tabs, current Git branch). When retrieving, heavily penalize vector results that don't match the current topological context. A memory formed while editing backend/api/users.py should trigger easily when editing backend/api/posts.py, but remain dormant when editing frontend/styles.css.
8. Zeigarnik Effect
a) What it says: People remember uncompleted or interrupted tasks better than completed tasks. The cognitive tension keeps the memory highly activated.
b) Mapping to umx: Tracking unfinished debugging sessions, active PRs, or dangling TODOs across sessions.
c) Design Change (Actionable):
Yes, algorithmic modification. Add a resolved: boolean flag to task/goal-oriented memories. If resolved == false, apply a constant additive boost (+X) to its activation score, bypassing the ACT-R decay curve entirely. When a human boots up umx on Monday morning, the agent's most activated memory shouldn't be what it did right before logging off on Friday; it should be the half-finished refactor it left broken. Once marked resolved == true, normal decay resumes immediately.
9. Metacognition / Feeling of Knowing (FOK)
a) What it says: The ability to judge whether you possess a piece of knowledge before you actually retrieve the details (the "Tip of the Tongue" phenomenon).
b) Mapping to umx: Gap-detection, preventing expensive RAG lookups for things it doesn't know, and knowing when to search the codebase.
c) Design Change (Actionable):
Yes, architectural change (Two-Pass Retrieval). Introduce a lightweight "Knowledge Index" distinct from the heavy Vector DB. This is a condensed map of what umx has seen (e.g., a tree of known file names and API endpoints, stored in the system prompt or a very fast local cache).
Pass 1 (Metacognition): The LLM checks the Knowledge Index. It decides, "I have a high feeling of knowing for stripe_webhooks.py, but a low feeling of knowing for paypal_api.py."
Pass 2 (Action): It actively requests to query the DB for the Stripe file, but triggers a web-search or asks the user for the PayPal docs. This prevents the agent from hallucinating code for files it confidently knows it hasn't mapped yet.
---
claude
Good, I have solid material for all 9 theories. Let me now write this up as the deep, actionable analysis Boz asked for.

This is a substantial document worth doing right. Let me write this as prose — no lists, no headers per theory, just a sustained analytical argument. It deserves careful treatment, not bullet-point decoration.

Here is a rigorous per-theory analysis. For each, the verdict is explicit: design change required, design change optional, or citation only. No decoration.

1. Interference Theory (Proactive/Retroactive)
What it says. Interference theory posits that forgetting is predominantly due to other information in long-term memory interfering with retrieval. Retroactive interference occurs when new information impairs recall of previously acquired memories; proactive interference is the reverse — older memories interfere with retrieval of newer ones. 
ScienceDirect
 Critically, the similarity of the competing memories determines the severity: when new information is presented alongside very similar old material, retroactive interference is at its worst — participants who learned synonyms showed the most forgetting, while those who learned numbers showed the least. 
Vaia

How it maps to umx. This is the core unsolved problem in any AI knowledge base and umx has no mechanism for it yet. Consider: a project switches from requests to httpx. umx stores both facts. When the agent retrieves "how do I make HTTP calls in this project," it gets both memories, potentially with nearly equal activation strength (ACT-R base-level learning doesn't know they're contradictory — it just tracks frequency/recency). The human brain resolves this through retroactive interference — the new fact suppresses the old one. umx has no analog.

The mechanism needed is an explicit contradiction detector. When a new fact is written whose semantic content contradicts an existing memory at the same scope/key, one of three things should happen: (a) the old memory gets a superseded_by pointer and its activation strength is zeroed or penalized, (b) both are retained but tagged with conflicts_with: [id] for the retrieval layer to expose the conflict rather than silently returning the highest-activation entry, or (c) the dream pipeline adjudicates during consolidation.

The Associative Unlearning Hypothesis explains retroactive interference by arguing that new associations replace old ones in memory, causing the participant to forget the initial associations — not merely suppress them. 
Psynso
 This maps to option (a): explicit unlearning via superseded_by, not just reduced activation. The important design point is that similarity-gated interference means you only need the detector for semantically proximate facts — "preferred HTTP library" conflicts with another "preferred HTTP library" entry, but not with a fact about test fixtures. The scope hierarchy you already have is the right gate for this.

Verdict: design change required. Add a conflicts_with field to the memory schema and a conflict-detection pass in the write path (or the dream pipeline). Without this, the encoding strength model is misleading — a fact at strength 4 that was true six months ago will compete incorrectly with a strength-3 fact that's current.

2. Bartlett's Schema Theory & Reconstructive Memory
What it says. Bartlett (1932) argued that remembering is "an imaginative reconstruction or construction" that depends heavily on schema — an active organisation of past reactions or experiences that operates in any well-adapted response. Remembering is not the re-excitation of fixed, lifeless traces but a reconstruction guided by existing knowledge structures. 
PubMed Central
 His famous experiment showed that when participants recalled a story from an unfamiliar culture, their recollections were generally shorter than the original, and aspects that conflicted with their existing schematic knowledge were removed or altered to fit more personally relevant versions. 
Wikipedia

How it maps to umx. This is the most underappreciated property of the dream pipeline. You named the pipeline after sleep consolidation, but Bartlett's insight is the deeper mechanism at work when an LLM summarizes episodic memories into semantic ones. The LLM doesn't faithfully compress — it reconstructs. It applies its own massive schema (everything in its training weights) to the raw episodic content, and the output reflects the intersection of the input and the schema, not the input alone. This is not a bug to prevent; it's the mechanism to design around.

The concrete implication: when umx uses an LLM to convert episodic memory nodes (raw session logs) into semantic memory nodes (distilled facts), the distillation prompt needs to be schema-aware and schema-explicit. If you just ask the LLM to "summarize what was learned," it will produce schema-filtered output with no record of what was filtered. You need to prompt it to output both the distilled fact and any content it found surprising, culturally foreign to its schema, or ambiguous — these are the Bartlett-ian "alien elements" that get dropped. Those dropped elements are precisely the high-signal, project-specific idiosyncrasies that generic AI knowledge wouldn't know to keep.

Concretely: the dream pipeline prompt should include an explicit instruction like "flag any project-specific conventions that deviate from common practice" — this forces the LLM's schema to surface rather than silently absorb the deviation. The flagged items get a schema_conflict: true tag and higher encoding strength by default.

Verdict: design change required in the dream pipeline prompt. Not an architectural change — a prompt engineering change with a new tag. Bartlett predicts exactly where LLM-based summarization will fail; the fix is to make the failure visible rather than invisible.

3. Spacing Effect / Testing Effect
What it says. Ebbinghaus discovered that spacing reviews over time produced far more durable retention than massing practice into a single session — the spacing effect. Every time the brain successfully retrieves a memory, the synaptic connections are briefly destabilized and then reconsolidated in a slightly stronger, more stable form (reconsolidation). The harder the retrieval, the stronger the reconsolidation — struggling to remember something and succeeding is neurologically more valuable than remembering it effortlessly. 
Very Big Brain

This is distinct from the forgetting-curve aspect of Ebbinghaus you already cite. The forgetting curve is about decay; the spacing effect is about strengthening through retrieval. They're related but not the same mechanism.

How it maps to umx. The ACT-R base-level learning equation you use does partial credit for this — retrieval strengthens traces in ACT-R. But umx is passive: it waits for retrieval events to happen organically. The spacing effect implies a more active design. Every time a memory is retrieved successfully, its last_retrieved_at timestamp and retrieval count should be updated. But more importantly, a memory that hasn't been retrieved in a while should not just have its activation decay to zero — it should be scheduled for a "probe" by the dream pipeline, precisely because the moment of near-forgetting followed by successful retrieval is the optimal consolidation event.

A profound consequence of the spacing effect is that the more reviews are completed with appropriate spacing, the longer the memory will be retained, and the longer one can wait until the next review is needed — giving rise to spaced repetition systems that schedule reviews automatically based on decay. 
Justin Skycak
 This is the SRS (spaced repetition system) insight: the optimal review is scheduled at the point of near-forgetting, not at a fixed calendar interval and not on demand.

For umx, this means the dream pipeline should include a review scheduler: memories whose projected activation (from the ACT-R formula) will fall below a retrieval threshold within the next N sessions get surfaced in the next dream run as "reinforce these." The dream pipeline then either (a) embeds them in a summary that forces re-encoding, or (b) flags them for explicit injection into the next agent context. This turns the dream pipeline from a purely backward-looking consolidation step into a forward-looking maintenance scheduler.

Verdict: design change required. The ACT-R citation covers decay; the spacing effect requires an active SRS scheduler in the dream pipeline. These are different mechanisms and the current spec conflates them.

4. Levels of Processing (Craik & Lockhart 1972)
What it says. The central claim of LOP theory is that the depth to which information is mentally analysed during initial exposure determines its memorability. Processing occurs on a continuum from shallow sensory analysis to deep semantic analysis. The deeper and more elaborative the processing during encoding, the stronger and longer-lasting the memory trace. 
Simply Psychology
 Crucially, Craik & Lockhart demonstrated that deep processing — active engagement with feedback — produces stronger traces than shallow processing. Reading about a procedure does not form a robust gist; performing it and receiving feedback does. 
ResearchGate

How it maps to umx. This is a direct justification for differentiating encoding strength (your 1–5 scale) by how a memory was created, not just how often it was accessed. In umx terms:

Strength 1 (surface): memory was extracted by scanning text or inferring from context — the agent never acted on it.
Strength 2–3 (phonological/structural): memory was used to make a decision but the outcome was not observed.
Strength 4–5 (semantic/elaborative): memory was used, the agent acted, the action succeeded or failed, and the outcome was recorded.
The coding implication is that the encoding strength assigned at write time should be informed by the provenance type of the memory. A fact the agent read in a README gets a lower initial strength than a fact it derived from debugging a failing test. Both might be true; only one was learned through deep processing.

This also maps to the dream pipeline: when consolidating episodic memories, the pipeline should weight semantic memories that were created through action-outcome cycles more heavily than those created through passive reading. The LOP framework provides the cognitive science rationale for this weighting.

Verdict: design change optional but well-motivated. The encoding strength model already exists; LOP provides a principled taxonomy for the write-time strength assignment rules. If the spec currently assigns initial strength arbitrarily or by source type alone, LOP formalizes the design. Worth a short subsection in the spec.

5. Source Monitoring (Johnson, Hashtroudi & Lindsay 1993)
What it says. Source monitoring refers to the ability to remember the origin of information — where and how a memory was acquired. 
ScienceDirect
 The source monitoring framework treats source as a multidimensional construct with indefinitely many possibilities — including when and where information was encountered, the medium in which it was presented, and the agent who delivered it. 
Online Academic Community
 Source monitoring errors range from trivial to serious: from telling a joke to the same person you heard it from, to mistaking a memory of a dream for a memory of a real event. 
Encyclopedia.com

How it maps to umx. This is arguably the most directly actionable of the nine theories for umx, because umx already has the right data model in principle — the filesystem-native design preserves scope hierarchies and file timestamps — but the spec doesn't explicitly design source monitoring as a first-class concern.

The failure modes Bartlett predicted (schema contamination during summarization) become traceable if source monitoring is built in. Every memory node should carry not just the content and encoding strength, but a structured provenance record:

yaml
source:
  type: "agent_observation" | "user_explicit" | "dream_consolidation" | "inference" | "external_doc"
  session_id: ...
  timestamp: ...
  parent_memory_ids: [...]  # for derived/consolidated memories
  confidence: float         # for inferences
The dream_consolidation source type is critical: it tells the agent "this fact was generated by an LLM summarizing other memories" — which means it's subject to Bartlett-ian distortion and should carry lower confidence than a directly observed fact at the same encoding strength.

The source monitoring framework also addresses cryptomnesia and the incorporation of fiction into fact — disruptions that occur when source metadata is absent or confused. 
PubMed
 For an AI agent, this is the hallucination problem: the agent "remembers" something confidently but the source was an LLM inference, not an observation. Source monitoring gives you the metadata to detect and flag this class of error at retrieval time.

Verdict: design change required. This is not a citation — it's a schema requirement. The source field with type, session_id, and parent_memory_ids should be a required field in the memory node schema, and the retrieval layer should expose source type alongside activation strength.

6. Consolidation Theory & the Dream Pipeline
What it says. Consolidation theory proposes that new memories are fragile when first formed and are strengthened and stabilized over time. Consolidation is thought to be an opportunistic process that occurs especially during behavioral states of reduced interference — quiet rest and sleep. 
ScienceDirect
 When new information is presented at a time when the old memory has not yet been consolidated, retroactive interference occurs — the old memory is disrupted before it stabilizes. 
Vaia

How it maps to umx. You named the pipeline after this correctly, but the current spec describes the dream pipeline primarily as a summarization step (episodic → semantic compression) rather than implementing the full consolidation mechanism. The cognitive science predicts several behaviors the pipeline should exhibit that go beyond summarization:

First, temporal fragility: a memory written in session N should be explicitly marked as consolidation_status: "fragile" until the dream pipeline has run. Fragile memories should not be promoted to semantic tier or have their strength increased by subsequent access — the reconsolidation window needs to close first. This prevents a buggy session from overwriting consolidated knowledge before the pipeline can adjudicate.

Second, interference suppression during consolidation: the consolidation literature shows that consolidation works best in a low-interference state. For umx, this means the dream pipeline should run on the full memory corpus for a project scope, not just the most recent session — otherwise recent sessions interfere with older consolidated memories without the pipeline having a global view to detect the conflict.

Third, reactivation-triggered reconsolidation: when a memory is retrieved, the synaptic connections are briefly destabilized — the memory enters a reconsolidation window where it can be strengthened or modified. 
Very Big Brain
 In umx terms: every retrieval event should open a brief window where the retrieved memory's encoding strength can be updated (upward on successful use, downward on disconfirmation). The dream pipeline should flush these pending updates before running the next consolidation pass.

Verdict: design change required. The consolidation_status field and the temporal fragility window are not in the spec and should be. The dream pipeline needs to be reframed as implementing the full consolidation loop, not just compression.

7. Encoding Specificity (Tulving & Thomson 1973)
What it says. The encoding specificity paradigm indicates that memory recall is enhanced when contextual factors are congruent between encoding and retrieval — the classic example being that recall is better when study and test occur in the same room. 
PubMed Central
 More precisely: to the extent a retrieval cue matches or overlaps the memory trace of an experience, it will be effective in evoking the memory. 
Noba

How it maps to umx. This is the strongest argument for the scope-based retrieval hierarchy you already have, plus an argument for richer context tagging at write time.

The implication is that a memory written during a debugging session for module auth should be tagged with the context that was active at encoding: the current task type, the relevant module, the active error class, the current phase of the project. When an agent later retrieves memories, the retrieval query should match not just semantic content but context — a memory about "JWT expiry handling" retrieved during a JWT debugging session should score higher than the same memory retrieved during a database migration session, even if both are "relevant" by content.

The current filesystem hierarchy (project → component → session) is a coarse approximation of this. A richer implementation would store a encoding_context object alongside each memory:

yaml
encoding_context:
  task_type: "debugging" | "feature_development" | "refactoring" | "review"
  active_module: "auth"
  error_class: "TokenExpiredError"
  project_phase: "integration_testing"
Retrieval would then score memories by both semantic similarity and context overlap, giving a retrieval-context-match bonus. This is the mechanism behind why "you remember better in the room where you learned it" — it's not magic, it's context-feature overlap.

Verdict: design change optional but architecturally clean. The scope hierarchy already approximates this; adding encoding_context with structured fields would let the retrieval layer implement the full encoding specificity bonus. High ROI if retrieval precision is a problem in practice.

8. Zeigarnik Effect
What it says. The Zeigarnik effect postulates that people remember unfinished or interrupted tasks better than completed tasks. The advantage of remembrance is explained by task-specific tension: an incomplete task establishes cognitive tension that improves the accessibility of relevant content. The tension is relieved upon completion but persists if interrupted. 
Wikipedia

However, note the important caveat: a 2025 systematic review and meta-analysis found no memory advantage for unfinished tasks but found a general tendency to resume tasks — the Ovsiankina effect. The Zeigarnik effect lacks universal validity, whereas the Ovsiankina effect represents a general tendency. 
Wikipedia

How it maps to umx. The Ovsiankina effect (tendency to resume) is more robustly supported than pure memory advantage, and it's the one that matters for AI coding agents. The design implication isn't "incomplete tasks are remembered better" — it's "incomplete tasks maintain cognitive salience and should be automatically re-injected into context at session start."

This maps cleanly to a task_status field on memory nodes: open | blocked | resolved. An open task-type memory should receive a salience bonus at retrieval — not because its encoding strength is higher, but because it carries unresolved state. At the start of each new session, the agent context should be seeded with open task memories regardless of their activation strength, specifically to replicate the Ovsiankina resumption tendency.

The practical benefit: an agent debugging a race condition, forced to context-switch to an unrelated feature, should reliably "pick up where it left off" when returning to the race condition. Without explicit task status tracking, the interrupted debugging thread competes on equal terms with everything else in the memory corpus. With it, open + task_type: debugging + module: scheduler gets injected directly.

Verdict: design change required, but cite Ovsiankina not Zeigarnik. The task_status field is a necessary addition. But be precise: the supporting effect is Ovsiankina (resume tendency), not the Zeigarnik memory-advantage claim, which is contested.

9. Metacognition / Feeling of Knowing (Nelson & Narens 1990)
What it says. Nelson and Narens documented the paradoxical finding that people can accurately predict their subsequent likelihood of recognizing non-recalled items, and that they can quickly and accurately decide — on the basis of no more than a cursory search through memory — that they will not retrieve particular sought-after items. 
ScienceDirect
 The framework distinguishes monitoring (knowing that you know or don't know) from control (taking action based on that knowledge). The metacognitive decision to initiate a retrieval search is based on a very rapid, preliminary feeling-of-knowing judgment — similar to the "Jeopardy" player who signals immediately that they can answer before actually retrieving the answer. 
UC Irvine Language Science

How it maps to umx. This is the theoretical basis for umx's gap-detection mechanism, but the current framing is incomplete. Gap detection in most AI memory systems is reactive: the agent notices it doesn't know something and queries the memory store. The Nelson/Narens insight is that metamemory is a two-level system — there's a meta-level model of what the object level contains, distinct from the object level itself. The meta-level can report "I know that I don't know X" (accurate gap detection) or "I know that I know X but can't retrieve it right now" (tip-of-the-tongue, temporarily inaccessible).

For umx, this means the memory system needs a manifest — a lightweight index of what domains/topics/modules the memory store covers, distinct from the memories themselves. The manifest is the meta-level model. When an agent queries about a topic not in the manifest, the gap is confirmed with high confidence (true absence). When a topic is in the manifest but retrieval fails, that's a retrieval failure on an existing memory (potentially a consolidation status issue or an interference problem). These two failure modes require different responses.

The manifest also enables proactive gap detection: before starting a session on module X, the agent checks the manifest, finds no memories for module X, and explicitly flags "no prior knowledge of this module" to the user — rather than silently operating without context and potentially hallucinating familiarity.

In Nelson and Narens' framework, monitoring involves knowing that you know or do not know some information; control involves taking corrective action based on that knowledge. 
PubMed Central
 The manifest implements monitoring; the gap-flagging behavior implements control.

Verdict: design change required. The manifest/meta-level index is architecturally distinct from the memory store itself and should be explicitly specified. It's lightweight — a structured list of covered domains and their memory counts — but its absence means gap detection is indistinguishable from retrieval failure.

Summary: What Actually Changes the Design
Five of the nine require new schema fields or pipeline behavior, not just better citations:

The conflicts_with field and contradiction detection pass (Interference Theory) prevents false confidence in superseded facts. The source provenance field with type and parent_memory_ids (Source Monitoring) makes the dream pipeline's Bartlett-ian distortions traceable. The consolidation_status: fragile field and temporal fragility window (Consolidation Theory) prevents premature promotion of unstabilized memories. The task_status: open | blocked | resolved field with Ovsiankina-driven salience at session start (Zeigarnik/Ovsiankina) restores interrupted work threads reliably. The domain manifest as a meta-level index (Metacognition/Nelson-Narens) separates true gaps from retrieval failures.

The remaining four — Levels of Processing, Bartlett's dream pipeline prompt change, the SRS scheduler in the dream pipeline, and Encoding Specificity context tags — are real improvements but can be implemented incrementally without blocking the v0.3 merge. The five above should be in v0.3.
---
codex:
You’ve already got a strong foundation (especially with Endel Tulving, John R. Anderson, and Hermann Ebbinghaus). The gap now is less about more memory types and more about memory dynamics under stress, ambiguity, and iteration—which is exactly where these theories help.

Below is a tight, implementation-focused pass on each concept.

1. Interference Theory (Proactive & Retroactive)

(a) Theory
Older memories interfere with new learning (proactive interference), and new memories overwrite or distort old ones (retroactive interference). This is especially strong when memories are similar.

(b) Mapping to umx
This is directly about contradictory facts and code evolution.

Concrete mechanisms:

Similarity-weighted suppression
When a new memory conflicts with an old one, don’t overwrite.
Store both, but dynamically reduce retrieval probability of the older one proportional to semantic overlap.
Conflict sets
Group mutually inconsistent memories into a “conflict cluster”
Retrieval returns one candidate + uncertainty flag
Temporal arbitration
Recent memories get a boost only within a similarity band

(c) Impact
Major design change.
You likely need:

conflict detection layer (embedding + symbolic diff)
retrieval-time arbitration instead of static scoring

👉 Without this, your system will hallucinate consistency where there is none.

2. Schema Theory & Reconstructive Memory (Bartlett, 1932)

(a) Theory
Frederic Bartlett showed memory is reconstructed using schemas, not replayed verbatim.

(b) Mapping to umx
This is exactly your “LLM summarization = JPEG compression” idea.

Concrete mechanisms:

Schema-layer abstraction
Store:
raw trace (episodic)
compressed schema (semantic abstraction)
Reconstruction pipeline
Retrieval = schema + partial traces + inference
Schema drift tracking
Detect when summaries diverge from original traces over time

(c) Impact
Major design upgrade.

Justifies lossy compression with guardrails
Requires:
dual storage (trace + abstraction)
reconstruction audit (compare regenerated vs original)

👉 Without this, summarization silently corrupts knowledge.

3. Spaced Repetition & Testing Effect

(a) Theory
Memory strengthens more from retrieval than re-exposure, and spacing improves retention.

(b) Mapping to umx
Your system currently uses decay (Ebbinghaus) but not active strengthening.

Concrete mechanisms:

Retrieval-as-write
Every successful retrieval updates strength
Spaced reactivation scheduler
Periodically “query” important memories
Failure-driven reinforcement
If retrieval fails → boost priority

(c) Impact
Major design addition.

Introduces active memory maintenance loop
Moves from passive store → self-healing memory system

👉 This is one of the highest ROI additions.

4. Levels of Processing (Craik & Lockhart, 1972)

(a) Theory
Deeper processing → stronger memory encoding.

(b) Mapping to umx
Not all memories are equal—depends on how they were formed.

Concrete mechanisms:

Encoding depth score
shallow: logs, passive observations
deep: reasoning, debugging, synthesis
Weight memory strength by depth
Force elaboration on critical writes
e.g., “explain why this fix works” before storing

(c) Impact
Moderate but valuable design change.

Improves signal quality of memory store
Prevents noise accumulation

👉 This is a quality filter, not a structural overhaul.

5. Source Monitoring (Johnson et al., 1993)

(a) Theory
Memory includes source attribution (where it came from), which can fail → false memories.

(b) Mapping to umx
This is critical for provenance and trust.

Concrete mechanisms:

Attach metadata:
origin: user / tool / inference / external API
confidence tier
Source-aware retrieval
allow filtering (e.g., “only user-confirmed facts”)
Source conflict detection
contradictions weighted by source reliability

(c) Impact
Essential design requirement.

Not optional if system interacts with external tools or users

👉 Without this, you cannot reason about truth.

6. Consolidation Theory (Systems + Sleep)

(a) Theory
Memories are initially fragile and stabilize over time, often during offline processing.

(b) Mapping to umx
Your “dream pipeline” is aligned but likely underpowered.

Concrete mechanisms:

Two-stage memory
volatile buffer (fast, noisy)
consolidated store (stable, structured)
Offline consolidation jobs
deduplicate
merge related memories
compress into schemas
Replay simulation
re-run past traces to strengthen structure

(c) Impact
Major architectural component.

Requires:
asynchronous processing layer
memory lifecycle states

👉 This is what prevents your system from becoming a junk drawer.

7. Encoding Specificity (Tulving & Thomson, 1973)

(a) Theory
Recall is best when retrieval context matches encoding context.

(b) Mapping to umx
This directly informs scope-based retrieval.

Concrete mechanisms:

Store context vectors:
task, repo, file, goal state
Context-gated retrieval
prioritize memories encoded in similar contexts
Context reinstatement
rehydrate prior working state before retrieval

(c) Impact
Major retrieval improvement.

Reduces irrelevant recall dramatically

👉 This is key for multi-project or multi-task agents.

8. Zeigarnik Effect

(a) Theory
Unfinished tasks are remembered better than completed ones.

(b) Mapping to umx
Perfect for debugging and task tracking.

Concrete mechanisms:

Open-loop tagging
mark incomplete tasks
Priority boost
unfinished tasks get higher retrieval weight
Auto-resurfacing
periodically bring unresolved items back

(c) Impact
Small but high-leverage feature.

Especially useful for long-running coding agents

👉 Think: “Why did we stop fixing that bug?”

9. Metacognition / Feeling of Knowing

(a) Theory
You can know that you know something without retrieving it.

(b) Mapping to umx
This is your gap-detection system.

Concrete mechanisms:

FOK score (feeling-of-knowing)
based on partial activation signals
Trigger targeted search
“I know this exists → expand retrieval radius”
Distinguish:
unknown vs known-but-not-retrieved

(c) Impact
Major intelligence upgrade.

Enables:
better query planning
fewer hallucinations

👉 This is what separates search from thinking.

What Actually Changes the System
High-impact (you should implement)
Interference theory → conflict-aware memory
Spaced repetition/testing → active strengthening loop
Consolidation → lifecycle + offline processing
Encoding specificity → context-aware retrieval
Source monitoring → provenance layer
Metacognition → retrieval control system
Medium-impact
Schema/reconstructive memory → improves summarization robustness
Levels of processing → improves encoding quality
Targeted feature
Zeigarnik effect → task persistence layer
The Big Missing Idea (Synthesis)

Right now, umx likely treats memory as:

stored → decays → retrieved

These theories collectively push it toward:

encoded (with depth & context) → competes (interference) → evolves (reconstruction) → strengthens (retrieval) → stabilizes (consolidation) → is queried strategically (metacognition)

That’s a fundamentally different system:

less like a database
more like a self-organizing, self-correcting memory ecology

If you want, I can turn this into a concrete architecture diagram or pseudo-spec for umx (data structures + retrieval algorithm).
---
Question 2:
gemini:
Based on Andrej Karpathy’s extensive talks (such as his famous "State of GPT" presentation), his writings, and his active presence on X (formerly Twitter), his views on memory in Large Language Models (LLMs) and agents revolve largely around the paradigm of the "LLM as an Operating System (LLM OS)."
Here is a summary of his key patterns, insights, and views on AI memory systems:
1. Context Windows vs. Persistent Memory
Karpathy popularized the "LLM OS" analogy (he heavily discussed this at Microsoft Build in May 2023 and on X throughout late 2023 and 2024).
Context Window = RAM: He views the context window as the system's "working memory" or RAM. It is fast, readily available for the "CPU" (the LLM's core reasoning weights) to process, but it is ephemeral.
Persistent Memory = Disk Storage: Persistent memory is the external file system. Just as a traditional computer doesn't load all its files into RAM at once, an AI system shouldn't load all its data into the context window. It needs a mechanism to save and load data from an external "disk" (like a vector database or standard SQL database).
2. Managing Memory Patterns (Short-Term, Long-Term, Episodic)
Karpathy has broken down how agents should manage different tiers of memory:
Short-Term Memory: Handled entirely in-context. It holds the immediate instructions, recent conversation turns, and the "scratchpad" (Chain of Thought).
Long-Term/Episodic Memory: Karpathy notes that simply appending every interaction to an endlessly growing context window is inefficient and brittle. Instead, agents must actively manage their memory streams. He advocates for architectures where an agent periodically "reflects" on its short-term memory, compresses it via summarization, and writes the important episodic takeaways to an external database to be retrieved later.
3. RAG vs. In-Context Learning vs. Fine-Tuning
Karpathy is highly definitive on this triad, frequently addressing the misconception that models should be fine-tuned to learn new facts:
Fine-Tuning: According to Karpathy, fine-tuning is for behavior, tone, style, and formatting—not for injecting factual knowledge. He likens trying to teach an LLM raw facts via fine-tuning to a very inefficient, lossy process.
RAG (Retrieval-Augmented Generation) & In-Context Learning: For injecting facts and memories, he strongly advocates for RAG. He famously tweeted that giving an LLM access to RAG is like giving a student an "open-book exam." The model retrieves the necessary long-term memories from the "disk," places them in the "RAM" (in-context learning), and reasons over them.
4. Endorsed vs. Critiqued Memory Architectures
Critique of Infinite Context as a Cure-All: While Karpathy has praised models with massive context windows (like Gemini 1.5 Pro's 1M-2M tokens), he cautions against treating massive context as a substitute for true memory architecture. Pushing too much into the context window leads to high compute costs, latency issues, and the "lost in the middle" attention degradation problem.
Endorsement of the Modular Agent Loop: He endorses an architecture where the LLM is the central processor surrounded by peripheral tools: a web browser, a code interpreter, and a discrete read/write memory system. The agent explicitly decides when to query the "disk" and when to write a memory down for future use.
5. Recent Statements (2024–2026) on Agent Memory
In mid-2024, Karpathy launched Eureka Labs, focusing heavily on AI-native education and AI tutors. His recent discourse on memory has heavily mapped to this domain:
The "Tutor" Episodic Memory: He has emphasized that the difference between a chatbot and an AI tutor is stateful, episodic memory. A true AI agent must remember that a student struggled with fractions three weeks ago and adjust its current teaching strategy accordingly.
Agentic Friction: In recent podcast appearances (such as his early 2024 appearance on the Lex Fridman Podcast, Ep. 415), he noted that while LLMs are incredibly smart, our wrapper architectures (how agents use memory and tools) are still relatively primitive. He views the engineering of autonomous read/write memory loops as the primary hurdle before we achieve highly reliable, autonomous AI agents.
Key Sources for Further Reading:
State of GPT – Microsoft Build Talk (May 2023)
Intro to Large Language Models – YouTube (November 2023)
Lex Fridman Podcast #415 (February 2024)
Eureka Labs Announcement & Vision (July 2024)
Karpathy's X (Twitter) Feed (@karpathy) - specifically threads on "LLM OS" and "RAG vs. Fine-tuning".
---
claude
I now have comprehensive material to write a thorough summary. Let me compile the findings.

Andrej Karpathy on Memory in LLMs and AI Agents: Key Views & Patterns (2024–2026)

1. Context Window vs. Persistent Memory
Karpathy has been unusually direct about the fundamental tension between short-lived context and durable memory. In a March 2025 tweet, he laid out two poles: starting a "new conversation" per request (stateless) vs. one giant thread forever, arguing the "New Conversation" button is likely just a developer convenience—an internal implementation detail surfaced to the user—and that the real solution is "a very well-implemented memory feature, along the lines of active, agentic context management. Something I haven't really seen at all so far." X
He also enumerated four concrete reasons why simply enlarging the context window doesn't solve memory:

Speed & cost: larger windows cost more compute and are slower.
Attention degradation: just because you can feed in all those tokens doesn't mean they can be effectively manipulated—the "needle in the haystack" eval demonstrates this. X
Signal-to-noise ratio: too many tokens competing for attention may decrease performance by being too "distracting," diffusing attention too broadly. X
Train/test distribution mismatch: most finetuning data is short or single-turn; giant conversations push the LLM into a distribution it hasn't been trained on much, and human labelers simply cannot write or verify "optimal responses" for a conversation of hundreds of thousands of tokens. X

Source: X/@karpathy, March 2025

2. The Context Window as RAM / LLM as OS
Karpathy has popularized a powerful architectural metaphor. He described LLMs as "a new kind of operating system," where the LLM serves as the CPU and its context window functions as RAM—the model's working memory. Galileo AI This framing is central to his thinking: what you put in the context window is working memory, and managing it well is the core engineering challenge.
This led directly to his coining of "context engineering" in mid-2025. He stated that "context engineering is the delicate art and science of filling the context window with just the right information for each step," arguing the term is better than "prompt engineering" because real industrial LLM applications involve carefully assembling everything the model needs—instructions, data, examples, tools, state history, retrieved facts—all packaged into the context at runtime. Substack
Source: X/@karpathy, mid-2025; YC AI Startup School Talk, 2025

3. RAG vs. In-Context Learning vs. Fine-Tuning
This is where Karpathy's most recent and concrete contribution lies—with a direct critique of RAG and a proposed alternative.
Against standard RAG (April 2026): He argued that most RAG systems—including NotebookLM, ChatGPT file uploads, and typical vector-DB pipelines—have no accumulation. The LLM rediscovers knowledge from scratch on every query; "ask a subtle question that requires synthesizing five documents, and the LLM has to find and piece together the relevant fragments every time. Nothing is built up." GitHub
His alternative — LLM Knowledge Bases (wiki-as-memory): In his April 3, 2026 post and accompanying GitHub gist, he described spending a large fraction of his token budget on building a structured wiki maintained by the LLM itself—~100 articles and ~400K words—where the LLM auto-maintains index files and reads related data "fairly easily at this ~small scale," meaning he didn't have to reach for "fancy RAG." X
The system architecture is three-layered:

Raw/ — immutable source documents (articles, papers, repos, images)
Wiki/ — LLM-authored and LLM-maintained Markdown files with summaries, concept pages, and backlinks
Schema — a CLAUDE.md/AGENTS.md-style document telling the LLM the wiki's conventions

Critically, outputs from queries also get filed back into the wiki, so explorations compound. Periodic "linting" passes let the LLM scan for contradictions, stale claims, orphan pages, and missing cross-references. GitHub
Fine-tuning as the endgame: Karpathy's future direction points toward using a refined, linted wiki to generate synthetic training data and fine-tune a smaller model, so the LLM "knows" the researcher's personal knowledge base in its weights rather than just at inference time via context. VentureBeat
Sources: X/@karpathy, April 3, 2026; GitHub Gist karpathy/442a6bf

4. Specific Memory Architectures Endorsed or Critiqued
PatternKarpathy's StanceGiant single context threadSkeptical—distribution mismatch, cost, signal noise; no good training data for itNaive "New Conversation" statelessnessA temporary implementation detail, not the right long-term answerVector DB RAGFunctional but has no accumulation; re-derives knowledge every query; "black box" embeddings lack auditabilityLLM-maintained Markdown wikiActively uses and endorses; auditable, composable, compoundingCognitive core + external memoryEndorsed conceptually on Dwarkesh podcast — he argued today's LLMs are tasked with both being intelligent and memorizing the internet, and that this dual role is a handicap; future smaller models (~1B parameters) could act like humans who "look things up" via an external memory layer MemcoAgents with local context (Claude Code model)Strongly endorsed in 2025 Year in Review — the agent has access to your real local environment, existing files, secrets, and configuration rather than a blank cloud container

5. Agent Memory Systems (2024–2026)
December 2025 (software engineering thread): Karpathy described a "new programmable layer of abstraction" involving agents, subagents, their prompts, contexts, memory, modes, permissions, tools, plugins, skills, hooks, MCP, slash commands, and workflows—framing memory as one of several critical new primitives developers must master when building with agents. Futurum Group
2025 Year in Review (blog, Dec 2025): He highlighted Claude Code as the first convincing demonstration of what an LLM Agent looks like—something that "in a loopy way strings together tool use and reasoning for extended problem solving," and praised it specifically because it runs locally with access to the "already-existing and booted up computer, its installation, context, data, secrets, configuration, and the low-latency interaction." The key insight is that persistent local context (not a cloud container reset every run) is what makes agents actually work. Karpathy
Dwarkesh Podcast (2025): He introduced the concept of a "cognitive core"—a future architecture separating raw intelligence from knowledge storage, predicting highly capable small models augmented by active external memory systems. Memco
LLM Knowledge Bases (April 2026): He directly attacked the "stateless AI" problem—the dreaded context-limit reset that forces users to spend tokens reconstructing context the AI already "knew"—by proposing that the LLM itself act as a full-time research librarian maintaining a persistent, self-healing, auditable knowledge base. VentureBeat

Key Sources
SourceDateURLX thread: "New Conversation vs. One Giant Thread"March 2025https://x.com/karpathy/status/1902737525900525657X: "Context engineering" endorsementMid-2025https://x.com/karpathy/status/1937902205765607626YC AI Startup School Talk2025https://www.youtube.com/watch?v=LCEmiRjPEtQBlog: "2025 LLM Year in Review"Dec 19, 2025https://karpathy.bearblog.dev/year-in-review-2025/X: Software engineering "magnitude 9 earthquake" threadDec 26, 2025(reported at Futurumgroup.com)Dwarkesh Podcast2025https://www.dwarkesh.com/p/andrej-karpathyX: "LLM Knowledge Bases" postApril 3, 2026https://x.com/karpathy/status/2039805659525644595GitHub Gist: llm-wiki idea fileApril 3, 2026https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f

Summary of Karpathy's Core Mental Model on Memory

Context window = RAM, LLM = CPU. Memory management is the OS-level problem of AI systems.
Larger context windows are not a substitute for real memory architecture. They degrade on signal/noise, cost, and distribution mismatch.
Standard RAG is stateless and non-accumulating. It's a retrieval hack, not a memory system.
The right direction is an LLM-maintained, compounding knowledge store—auditable Markdown, not black-box vectors—where every query enriches the base.
Fine-tuning is the endpoint of this pipeline: bake the curated wiki into model weights for a true "personal intelligence."
Agents need local, persistent environmental context (files, config, history), not cloud containers reset per run.
"New Conversation" is a UX relic—the long-term answer is active, agentic context/memory management that has yet to be well-implemented.
---
codex:
Here’s a concise, source-backed synthesis of Andrej Karpathy’s views on memory in LLM systems (2024–2026), focusing on patterns rather than isolated quotes.

1) Context window vs. persistent memory
Core mental model
Karpathy repeatedly frames LLMs as:
Model weights = long-term memory (frozen)
Context window = working memory (ephemeral)
Analogy (widely cited from his talks/tweets):
“LLM is the CPU, context window is RAM”
Key insights
Context ≠ memory
It’s a temporary buffer that resets every session
Leads to “amnesia” across interactions
The real problem is not prompting, but:
what gets loaded into context and when
Emerging idea: “Context engineering”
Karpathy:
“Prompt engineering is writing a sentence; context engineering is writing the screenplay.”
Shift:
from crafting prompts → designing the full information state
Takeaway pattern

LLMs are fundamentally stateless processors; memory must be externally constructed and actively managed.

2) Memory structure patterns (short-term, long-term, episodic)

Karpathy doesn’t formalize a strict taxonomy, but his recent work implies a 3-layer memory stack:

1. Short-term memory (working set)
Recent conversation turns
Tool outputs, instructions
Lives entirely in the context window
2. Long-term structured memory (external)
His 2026 “LLM Knowledge Base”:
Persistent Markdown wiki maintained by the LLM
Structured, interlinked, continuously updated
Functions:
Stores distilled knowledge (not raw data)
Avoids recomputation across sessions
The LLM:
acts as “research librarian” maintaining this store
3. Parametric memory (weights)
Final stage:
knowledge → dataset → fine-tuning

Implied pipeline:

raw data → structured knowledge → context use → fine-tuning
Key pattern

Memory should evolve from raw → structured → compressed (weights)

3) RAG vs. in-context learning vs. fine-tuning
Karpathy’s evolving stance (2024–2026)
RAG (Retrieval-Augmented Generation)

Critiques:

Overly complex (embeddings, vector DBs)
Retrieves raw chunks, not synthesized knowledge
Produces:
redundancy
recomputation each query

“RAG… chops documents into chunks… similarity search…”

His alternative: “LLM Knowledge Base”
Replace RAG with:
curated, evolving Markdown knowledge
directly loaded into context
No embeddings / vector DB
Emphasis:
human-readable
LLM-maintained
compounding knowledge
In-context learning
Useful but limited:
bounded by context window
degrades with scale
Insight:
long context ≠ good memory
irrelevant context harms reasoning
Fine-tuning (endgame)
Implied trajectory:
Once knowledge stabilizes → move into weights
Community interpretation (aligned with his pipeline):
RAG = temporary
Fine-tuning = durable memory
Summary comparison (Karpathy-style)
Approach	Role	Limitation
Context window	working memory	ephemeral, small
RAG	retrieval layer	unsynthesized, brittle
Knowledge base (his approach)	structured memory	requires curation
Fine-tuning	permanent memory	costly, slower updates
4) Architectures he endorses / critiques
A. Endorsed: “LLM Knowledge Base” (2026)
Architecture
Inputs → raw files
LLM compiles → Markdown wiki
Wiki:
interlinked
summarized
continuously improved
Query:
relevant pages loaded into context
Properties
No vector DB
No embeddings
Self-improving
Auditable (human-readable)

“Structured Markdown wiki… compiled, linked, and maintained by the LLM”

B. Critique: Standard RAG pipelines
Too much infra for mid-scale problems
Treat knowledge as:
flat chunks instead of structured concepts
C. Endorsed pattern: “Compilation over retrieval”
Transform:
raw documents → semantic knowledge artifacts
Avoid:
repeated parsing of same data
D. Implicit architecture pattern

Karpathy is converging on:

LLM (stateless core)
+ external structured memory (wiki)
+ context assembly layer
+ optional fine-tuning loop
5) Recent (2024–2026) views on agent memory systems
A. Agents need persistent, compounding memory
Problem:
agents restart from scratch each session
His solution:
memory must accumulate and improve over time

News summary (2026):

Advocates personal knowledge bases
Systems that:
store experiences
improve responses over time
B. Memory should be active, not passive
LLM:
writes summaries
links concepts
cleans inconsistencies (“linting”)
Not just storage → continuous curation
C. Token economy insight (important nuance)
He emphasizes:
maximizing token throughput
Implication:
spend tokens on:
memory construction, not just generation

D. Shift: from “chatbot” → “knowledge system”
LLM becomes:
not just responder
but knowledge compiler + maintainer
Key unifying insights ( distilled )
1. Memory is the central unsolved problem
Not model capability
Not prompts

But:

how information persists and evolves

2. Context window is not memory
It is:
a projection of memory
assembled per step
3. Replace retrieval with compilation
Move from:
“search raw data”
To:
maintain structured knowledge
4. Memory should compound
Good systems:
get smarter over time
Bad systems:
recompute from scratch
5. The endgame = memory in weights

Pipeline:

interaction → knowledge base → dataset → fine-tuning
TL;DR (Karpathy’s implicit doctrine)
LLM = stateless reasoning engine
Context = temporary working memory
Real memory = external, structured, evolving
RAG = intermediate hack

Future =

LLM-native knowledge bases + eventual fine-tuned memory

If you want, I can map his ideas to concrete system designs (e.g., how to build this with tools like LangChain, vector DBs, or pure file-based agents).
---
Question 3:
gemini:

Note: As of April 8, 2026, the landscape of AI agent memory has transitioned from passive retrieval (standard RAG) to active, self-editing, and temporally-aware systems. Below is a comprehensive research summary on the state of the art in AI agent memory architectures.
1. Production Memory Systems in Major Frameworks
The major agentic frameworks have standardized around splitting memory into Working (Context), Episodic (Event Logs), and Semantic (Extracted Facts) layers.
Letta (formerly MemGPT): Pioneers the "Operating System" paradigm. Memory is strictly partitioned into Core Memory (always in the prompt, explicitly editable by the agent) and Archival Memory (out-of-core, paginated, searchable). Agents manage their own state via explicit function calling (e.g., core_memory_append, archival_memory_search). (Source: letta.com/docs)
LangGraph: Handles short-term/episodic memory via MemorySaver (thread-scoped checkpointing, allowing time-travel and human-in-the-loop). For cross-thread semantic memory, LangGraph uses the Store API, allowing agents to explicitly save and retrieve structured JSON namespaces. (Source:langchain.com/langgraph)
Mem0: A managed memory layer that focuses on automated Memory Synthesis. Instead of just storing vectors, Mem0 operates a background pipeline that extracts entities, infers preferences, and automatically resolves contradictions before they pollute the retrieval pool. (Source: mem0.ai)
Zep: Evolved into a robust Temporal Graph Memory engine. Zep continuously ingests dialogue and builds a continuous knowledge graph, tracking the validity of facts over time to naturally deprecate stale information without expensive LLM re-evaluations. (Source: getzep.com)
LlamaIndex: Heavily emphasizes Property Graphs (GraphRAG) combined with vector search. Their MemoryModule abstracts rolling summaries, while property graphs explicitly map relationships (e.g., [User] -> [Prefers] -> [Rust]). (Source: docs.llamaindex.ai)
2. Academic Literature & Research (2024–2026)
Recent papers focus heavily on graph-based memory and active self-management:
HippoRAG (2024): Inspired by the human hippocampus, this paper introduced personalized PageRank over Knowledge Graphs to enable complex, multi-hop memory retrieval that traditional vector stores fail at. (Source: arXiv:2405.14831)
MemoRAG (2024): Proposes "Memory-Augmented RAG," using a lightweight local model to continuously map out a global memory structure of the context, moving beyond naive text chunking. (Source:arXiv:2409.05591)
TiM (Think-in-Memory) & Self-Updating Agents (2025): A wave of late-2024 to 2025 papers shifted focus to "Active Memory," training LLMs to explicitly decide when to write, update, or delete state, rather than relying on automated passive extraction.
3. Memory in Coding Agents
Coding agents deal with massive contexts and require absolute precision, driving unique memory architectures:
Claude Code (Anthropic, 2025): Relies intensely on Prompt Caching rather than complex RAG. By loading massive directory structures and caching them cheaply, its "memory" is simply the live file system. For semantic rules, it explicitly reads/writes to structured Markdown files (e.g., CLAUDE.md or a .claude/ directory).
Cursor: Utilizes a hybrid memory model. Short-term memory is managed via UI chat limits, while long-term memory leverages BM25/Vector codebase indexing. For semantic memory, it uses explicit .cursorrules and .cursor/rules/ directories. Advanced features like Composer utilize intent-based AST (Abstract Syntax Tree) retrieval to pull relevant code chunks.
Devin (Cognition): Maintains an append-only Event Log (episodic memory). To prevent context limits, Devin employs aggressive background compaction (summarizing past tool calls into compressed states). It heavily utilizes "scratchpad files" (explicit structured notes) to maintain current goals, environment facts, and learned lessons.
4. Architectures & Approaches
Hierarchical Memory: Mimics human cognition (Working -> Episodic -> Semantic). Best for generalized agents.
Vector-Based Retrieval (Dense RAG): Fast, easily scalable. Pros: Cheap. Cons: Fails at multi-hop reasoning; easily loses temporal context.
Knowledge Graphs (GraphRAG): Extracts entities and edges. Pros: Unmatched for relationship mapping and complex queries. Cons: High token cost to construct and maintain.
Structured Notes (File-Based): Agents writing to explicit files (JSON/Markdown). Pros: High reliability, human-interpretable, perfect for coding. Cons: Doesn't scale well to millions of facts.
Summarization-Based: Rolling window summaries. Pros: Prevents bloat. Cons: Lossy compression; granular details are inevitably lost.
5. Trade-offs
Recall Quality vs. Cost: Generative KGs offer the highest multi-hop recall but cost significantly more in LLM tokens to continually re-map than passive Vector embeddings.
Staleness vs. Consistency: Overwriting old facts (Letta's self-editing) keeps memory fresh but risks an LLM accidentally deleting crucial context. Immutable, append-only memory (Devin's logs) is highly consistent but suffers from staleness unless a temporal resolution layer is applied.
6. Common Failure Modes
Memory Bloat: The system accumulates trivial facts ("User said 'thanks' at 4 PM"), polluting the retrieval pool, maxing out context windows, and increasing latency/costs.
Contradictory Memories: A vector database returning both "Architecture uses Next.js" (from Jan 2025) and "Architecture migrated to Go" (from Dec 2025). The LLM hallucinates because it doesn't know which is current.
Staleness: Retrieving deprecated API syntax or outdated rules because the memory chunk has high semantic similarity to the prompt.
Retrieval Irrelevance ("Lost in the Middle"): Dense embeddings pulling semantically similar but logically useless chunks that push vital instructions out of the LLM’s effective attention span.
7. Emerging Patterns (2025–2026)
Self-Editing Memory (CRUD Agents): Moving from passive RAG to agents treating memory like a database. Agents are natively equipped with create_memory, update_memory, and delete_memory tools.
Prompt Caching as Ephemeral Memory: With 1M–2M token context windows becoming cheaper in 2025/2026, "memory" is increasingly shifting back to simply stuffing the entire context into the prompt and caching it on the provider side.
Memory Compaction / Garbage Collection: Automated background processes (cron jobs) that consolidate, summarize, or delete unused memory nodes—acting like an OS garbage collector to prevent bloat.
Hybrid Retrieval (Graph + Vector + BM25): Standalone vector stores are largely deprecated in advanced agents. 2026 production systems use BM25 for exact keyword matches, Vectors for semantic drift, and Temporal Knowledge Graphs to understand how facts relate to one another over time.
---
claude
Now I have comprehensive material to write a thorough research summary. Let me compile this into a well-structured document.Now I have everything I need to write a comprehensive, well-sourced report. Let me produce the full research summary.

AI Agent Memory Architectures: State of the Art (April 2026)
This is a research-dense landscape to map, so I'll work through it section by section, with sources tied to dates.

1. The Big Picture
In 2026, memory is a first-class architectural component with its own benchmark suite, its own research literature, a measurable performance gap between approaches, and a rapidly expanding ecosystem of tools built specifically around it. Mem0 The framing of memory as "just RAG" has been retired. As of early 2026, the field has become the critical differentiator between toy demos and production-grade AI systems. Let's Data Science
The dominant academic taxonomy, from a December 2025 Tsinghua University survey, categorizes agent memory by function into three categories: factual memory, experiential memory, and working memory, and production agents implementing multiple memory types show measurably better task completion on multi-session benchmarks. Let's Data Science A complementary architectural taxonomy from a concurrent survey organizes memory by form: token-level, parametric, and latent memory, each offering distinct trade-offs in transparency, adaptability, and integration. Liner

2. Production Memory Systems in Major Frameworks
LangGraph + LangMem (LangChain)
LangGraph separates memory cleanly into two scopes. Short-term memory tracks the ongoing conversation by maintaining message history within a session via thread-scoped checkpoints. Long-term memory stores user-specific or application-level data across sessions, shared across conversational threads and scoped to any custom namespace. Langchain
LangMem (launched May 2025) extends this with three memory types. Episodic memory stores memories of past interactions, focusing on recalling specific experiences. Procedural memory represents internalized knowledge of how to perform tasks, saved across a combination of model weights, agent code, and agent prompts. LangMem's approach to procedural memory focuses on saving learned procedures as updated instructions in the agent's prompt. LangChain
The distinctive feature is automated prompt refinement. LangMem entirely eschews heavy external infrastructure and proprietary graphs, utilizing a highly flexible, flat key-value and vector architecture backed by LangGraph's native long-term memory store. Its primary objective is not just to track static user facts but to improve the agent's dynamic procedural behavior over time through automated prompt refinement and continuous instruction learning. Analytics Vidhya
Source: LangMem SDK Launch (May 2025); LangGraph Memory Docs

Mem0
Mem0 gives agents a three-tier memory system — user, session, and agent scopes — backed by a hybrid store combining vectors, graph relationships, and key-value lookups. When facts conflict, Mem0 self-edits rather than appending duplicates, keeping memory lean. Atlan Its graph variant (Mem0g), introduced January 2026, stores memories as directed labeled graphs where entities are nodes and relationships are edges.
On the LOCOMO benchmark (see §5), Mem0 reduced token usage and responded much faster, with a latency of just 1.44 seconds, a 92% improvement over full-context. Mem0g had slightly higher latency at 2.6 seconds but achieved the best accuracy among memory-augmented systems, using only 7k tokens per conversation versus the full-context approach's far higher overhead. Medium
Mem0's selective pipeline accepts a 6-percentage-point accuracy trade against full-context in exchange for 91% lower p95 latency and 90% fewer tokens. Mem0 The paper was published at ECAI 2025 (arXiv:2504.19413).
Source: Mem0 State of AI Agent Memory 2026 (March 2026); arXiv:2504.19413

Letta (formerly MemGPT)
Letta inverts the problem. Instead of bolting memory onto an existing agent, Letta becomes the platform where the agent lives, managing the memory system, the context window, and the tool execution loop. The agent has direct access to three memory tiers: core memory (like RAM, lives in the context window), recall memory (like disk cache, searchable conversation history), and archival memory (like cold storage, queried via tools). The agent edits its own memory — if a customer preference changes, the agent calls a memory-edit tool to update the relevant blocks. HydraDB
The June 2025 multi-agent Actor-Aware Memories feature is notable: a memory that reads "user needs help with deployment" is ambiguous about whether the user stated this directly, a monitoring agent inferred it, or a planning agent generated it as an intermediate step. Actor-aware memory tags each stored memory with its source actor, allowing a planning agent at retrieval time to filter for what the user actually said versus what another agent inferred. Mem0
In March 2026, Letta shipped a memory-first coding agent built on its platform. Source: atlan.com comparison (April 2026)

Zep
Zep models memory as a temporal knowledge graph. Facts aren't just stored — they're timestamped. When information changes, the old fact is marked invalid, not deleted. This enables queries that require chronological awareness. HydraDB
Zep excels in latency-sensitive, compute-heavy production environments. Its retrieval pipelines are heavily optimized, boasting average query latencies of under 50 milliseconds. For voice AI assistants, Zep provides a return_context argument that returns an updated context string immediately upon data ingestion, eliminating a separate retrieval round-trip. Analytics Vidhya
In open-domain LOCOMO questions, Zep achieved the highest F1 (49.56) and LLM-judge (76.60) scores, edging out Mem0's methods by a narrow margin, particularly for integrating conversational memory with external knowledge. arXiv There is an active benchmark dispute: Mem0's CTO filed a technical critique of Zep's LoCoMo claim, noting that Zep published only a single run while Mem0 reported averages of 10 independent runs with standard deviations, and that Zep used a modified retrieval template from its prior benchmarks, obscuring its system's true variability. GitHub
Source: HydraDB comparison (March 2026); Zep benchmark dispute (May 2025)

MemoryOS / MemOS
Two related but distinct projects tackle the "OS for memory" framing.
BAI-LAB MemoryOS (EMNLP 2025 Oral, arXiv:2506.06326): Hierarchically managed memory segments into short-term (real-time dialogue, chain-linked), mid-term (topic-segmented, similarity-grouped, heat-scored), and long-term (persistent user/agent traits) stores. Updates employ FIFO promotion from STM to MTM, and "heat"-scored promotion from MTM to LPM, supporting dynamic persona construction over thousands of conversational turns. On LoCoMo, the system shows average F1 improvements of 49.11% and BLEU-1 improvements of 46.18% over GPT-4o-mini baselines. Emergent Mind
MemTensor MemOS (arXiv:2505.22101, May 2025; full paper arXiv:2507.03724, July 2025): MemOS elevates memory to a first-class operational resource, building unified mechanisms for representation, organization, and governance across three core memory types: parametric, activation, and plaintext. arXiv Its MemCube abstraction provides standardized memory units with provenance tracking, versioning, and cross-agent portability. MemOS v2.0 (December 2025) added comprehensive knowledge base parsing, multi-modal memory (images/charts), tool memory for agent planning, and Redis Streams scheduling. GitHub

3. Coding Agent Memory
Coding agents have converged on file-based memory — a fundamentally different pattern from conversational agents.
Claude Code uses a multi-layer model. CLAUDE.md is a markdown file at the project root that Claude Code reads at the start of every session — use it to set coding standards, architecture decisions, preferred libraries, and review checklists. Claude also builds auto memory as it works, saving learnings like build commands and debugging insights across sessions. Claude
The leaked Claude Code source (March 31, 2026) revealed a three-layer memory compression architecture. The CLAUDE.md file isn't a config file in the traditional sense — it's a runtime constitution, loaded at session start, giving the agent standing context it doesn't have to rediscover every time. WaveSpeedAI
A common community pattern extends this: create .ai/memory.md that tracks what actually matters — document sessions in .ai/sessions/ with date-titled files covering session log, outcomes, and lessons learned; keep memories concise; adjust .ai/architecture.md and .ai/tech-stack.md when changes are made to project architecture or dependencies. Softcery
Cursor uses .cursorrules files with a similar philosophy. Cursor's normal mode has 128K token capacity and a Max Mode of 200K token capacity, though in practice it can reduce capacity as it may shorten input or drop older context to maintain performance speed. Qodo
The key distinction: Claude Code uses project-level Markdown memory files for codebase context. The Claude API itself remains stateless; developers building on it must implement their own memory layer. Let's Data Science
Devin and similar agents rely on a combination of repo-level context files and session-scoped working memory, with no published details on persistent cross-session memory at the time of writing.

4. Memory Approaches: Taxonomy of Architectures
(a) Vector-Based Retrieval
The baseline. Each memory is converted to an embedding and stored in a vector database (Qdrant, Pinecone, pgvector, ChromaDB). Retrieval is by cosine similarity. Fast to stand up; struggles with multi-hop queries and temporal reasoning. As of March 2026, purpose-built databases handle billions of vectors using HNSW indexing, where complexity grows logarithmically. Let's Data Science
(b) Knowledge Graphs / Temporal Graphs
The distinction between vector memory and graph memory is precise: vector memory retrieves semantically similar facts, while graph memory retrieves facts connected through relationships. Graph memory in AI agents was largely experimental in 2024. By early 2026, it is in production. Mem0
Zep's Graphiti engine uses bi-temporal graphs — every fact has a valid-time and transaction-time. Mem0g stores memories as directed labeled graphs. These handle "who knows what" and "what changed when" queries that vector search cannot.
(c) Hierarchical / Tiered Memory
Inspired by computer memory hierarchies. The Letta model (core/recall/archival), MemoryOS (STM/MTM/LTM), and MemOS all layer memory by cost and access speed. This motivates a layered memory hierarchy consisting of working memory, long-term storage, and cold archives, governed by recency, access frequency, and importance — similar to how OSes manage memory. arXiv
(d) Structured Notes / Schema-Based Memory
Memorilabs positions memory as structured, queryable, and trustworthy. Instead of treating memory as "similar text chunks," it treats memory as data with schema, constraints, and history — stored in relational databases such as Postgres or MySQL in normalized tables. This approach is especially powerful in enterprise, compliance, and multi-tenant environments where explainability and governance matter as much as recall quality. Medium
(e) Summarization / Compression-Based Memory
Used heavily in LangMem's "cold path" and in systems like SeCom. The agent (or a background process) periodically summarizes conversation history into compressed, structured notes. RAPTOR (2024) introduced recursive abstractive processing for tree-organized retrieval, building hierarchical summaries. A 2026 paper introduces an adaptive budgeted forgetting framework that assigns relevance scores to memory units and enforces constrained selection through optimization, integrating temporal decay, usage frequency, and semantic alignment to guide deletion decisions. arXiv
(f) Self-Editing / Procedural Memory
Agents actively rewrite their own instructions based on feedback. LangMem provides multiple algorithms for generating prompt update proposals, including a metaprompt approach that uses reflection and additional "thinking" time, a gradient approach that explicitly divides work into critique and prompt-proposal steps, and a simple prompt_memory algorithm. LangChain MemRL (Jan 2026) uses reinforcement learning on episodic memory for self-evolution.

5. The Benchmark: LOCOMO and Successors
LoCoMo spans up to 32 sessions with an average of ~600 turns (~16,000 tokens), incorporating images into conversational context. Each instance records session-level summaries, temporal metadata, long-term episodic memory objects, and images with captions. Human F1 on the benchmark is 87.9 — versus GPT-3.5 at 37.8 and the best RAG approach at 41.4. Emergent Mind
A February 2026 extension, LoCoMo-Plus, removes the strong semantic alignment between past context and current query that made LoCoMo tractable, testing "cue-trigger semantic disconnect" where models must retain and apply latent constraints across long conversational contexts. All evaluated methods exhibit a substantial performance gap between LoCoMo and LoCoMo-Plus, revealing that current systems largely equate conversational memory with explicit factual recall. arXiv
Current published LOCOMO standings (approximate, as of early 2026, using LLM-judge score):

MemoryOS: +49% F1 over GPT-4o-mini baseline
Zep: ~76–85% (disputed, see benchmark controversy above)
Mem0 + graph: ~75%
Mem0 (vector only): ~73%
LangMem: ~8 points below Mem0 on judge score
Full-context (GPT-4o): highest accuracy but ~17s p95 latency — unusable in production


6. Trade-Offs Between Approaches
ApproachRecall qualityCost (tokens/latency)Staleness handlingConsistencyFull contextHighestVery high (17s p95)N/APerfectVector RAGGood (single-hop)LowPoor — old facts retrieved equallyWeakGraph memoryBest (relational/temporal)Medium-highStrong (temporal edges)StrongHierarchicalGoodMediumDepends on promotion policyMediumSummarizationGoodLowGood if summaries refreshedLossySelf-editing promptsGood (procedural)Low at inferenceRequires active rewrite cyclesRisk of driftSQL/schema-basedExcellent (structured)LowExplicit versioningStrongest
The fundamental production tension is that the most important number in the benchmark table is not the accuracy column — it is the latency column for full-context: 9.87 seconds median, 17.12 seconds at p95. Full-context is technically the most accurate approach, but the only one that is categorically unusable in real-time production settings. Mem0
In complex multi-agent architectures such as those built on CrewAI or AutoGen, simultaneous read and write operations across a shared database dramatically worsen memory conflicts, requiring strict shared-memory access controls to prevent race conditions, circular dependencies, and cross-agent contamination. Analytics Vidhya

7. Common Failure Modes
Memory bloat: Agents store too much and retrieval quality degrades. The principle: store insights, not transcripts. "Student needs more practice with integration by substitution" is useful; a verbatim transcript is not. Let's Data Science
Contradictory memories / staleness: If the system identifies a change as a pivot, it must not simply delete the old memory. Instead, compressing it into a temporal reflection summary — e.g., "User utilized React until November 2025, but has since transitioned their primary stack to Vue" — explicitly preserves the historical evolution while strictly defining the current active baseline, preventing goal deviation or drift traps. Analytics Vidhya
False memory propagation: Benchmarks such as LOCOMO and LOCCO report performance degradation from 0.455 to 0.05 across stages, while MultiWOZ shows 78.2% accuracy with a 6.8% false memory rate under persistent retention. arXiv
Retrieval of irrelevant content: RAG-based memory faces challenges when questions require integrating knowledge across an entire session or learning from long, skill-encoding inputs — the retrieval mechanism, limited to top-k results, misses distributed or latent information. arXiv
Actor attribution in multi-agent systems: Covered in Letta's June 2025 Actor-Aware Memories (see §2). One agent's inference treated as ground truth by a downstream agent is a documented failure pattern.
Memory silos: The emergence of "memory silos" across platforms hinders the reuse and migration of prior interactions — a fundamental oversight in current LLMs that do not treat memory as an explicit, schedulable, and governable resource. arXiv

8. Emerging Patterns (2025–2026)
Self-editing / self-organizing memory. MemRL (Jan 2026) trains agents with RL on episodic memory. MemEvolve (Dec 2025) applies meta-evolution to memory systems. EverMemOS (Jan 2026) is a self-organizing memory OS for structured long-horizon reasoning. LangMem's procedural memory loops let agents rewrite their own system prompts.
Memory compaction. AI-centric MemoryOS architectures embed explicit lifecycle management, permitting creation, consolidation, migration, and selective forgetting of memory units. Emergent Mind The 2026 "adaptive budgeted forgetting" paper frames compaction as a constrained optimization problem.
Hybrid retrieval. Production systems increasingly combine vector similarity, graph traversal, and BM25 sparse search. Zep's Graphiti engine uses triple-modality retrieval. Structure-augmented RAG approaches (GraphRAG, RAPTOR, HippoRAG-V2, Cognee, Zep, Mem0) enhance retrieval with structural representations such as graphs or trees. arXiv
Actor-aware multi-agent memory. Tagging each memory with its source agent (user, monitor agent, planning agent) to prevent inference-laundering downstream.
On-device / local-first memory. SuperLocalMemory V3.3 (arXiv:2603.14588) presents a biologically-inspired local memory system with forgetting, consolidation, and compression with no cloud LLM dependency, achieving 87.7% on LoCoMo in its best configuration. All existing mainstream systems — Mem0, Zep, Letta/MemGPT — treat memory as a static store with flat retrieval. Memories are text entries in a vector database, retrieved by similarity search, and never transformed, compressed, or curated — contrasting with human memory's active processes of forgetting irrelevant details and consolidating episodes into general knowledge. arXiv
Memory portability. Mem0 added an export/import feature for moving memory between OpenMemory instances in September 2025, addressing the portability concern that comes with any local storage system. Mem0 In March 2026, Anthropic introduced a memory import tool for transferring histories from other providers.

9. Key Papers (2024–2026)
PaperDateKey contributionMemGPT / Letta (Packer et al.)Oct 2023OS-inspired tiered memory with agent-managed contextRAPTOR (Sarthi et al.)ICLR 2024Recursive abstractive processing for tree retrievalLoCoMo benchmark (Maharana et al.)2024Long-term conversational memory evaluation datasetA-Mem (Xu et al.)2025Agentic memory with dynamic linking and contextual tagsMem0 (Chhikara et al.)Apr 2025 / ECAI 2025Production-scale hybrid vector+graph memory; LOCOMO benchmarkZep / Graphiti (Rasmussen et al.)2025Temporal knowledge graph architecture for agent memoryMemOS short (Li et al.)May 2025Memory operating system concept; MemCube abstractionSeCom (Pan et al.)2025Segment-level memory with compression-based denoisingMemoryOS (Kang et al.)EMNLP 2025Hierarchical STM/MTM/LTM with heat-scored promotion; +49% F1MemOS full (Li et al.)Jul 2025Full MemOS architecture with lifecycle governance"Memory in the Age of AI Agents" survey (Liu et al.)Dec 2025Unified taxonomy (forms, functions, dynamics); paper list maintained at github.com/Shichun-Liu/Agent-Memory-Paper-ListLoCoMo-Plus (2026)Feb 2026Cognitive memory benchmark with cue-trigger semantic disconnectMAGMA (2026)Jan 2026Multi-graph based agentic memory architectureEverMemOS (2026)Jan 2026Self-organizing memory OS for long-horizon reasoningMemRL (2026)Jan 2026RL on episodic memory for self-evolving agentsSuperLocalMemory V3.3Apr 2026arXiv:2603.14588; biologically-inspired local memory with full cognitive taxonomy

10. Open Problems

Cognitive memory (LoCoMo-Plus gap): all current systems fail significantly when memory must constrain behavior rather than answer explicit factual queries.
Application-specific evaluation: LOCOMO scores don't transfer across domains. A system scoring 66% on LOCOMO may excel for coding and fail for healthcare.
Privacy and consent governance: what users can inspect, edit, and delete in their stored memories remains an application-layer concern with no standard.
Memory across providers: portability between memory systems is in early stages; most production deployments create vendor lock-in at the memory layer.
Forgetting without losing: structured, budget-aware forgetting that preserves semantic coherence remains an active research area.


Primary sources used:

mem0.ai/blog/state-of-ai-agent-memory-2026 (March 2026) — comprehensive production changelog analysis
atlan.com/know/best-ai-agent-memory-frameworks-2026 (April 2026) — framework comparison
analyticsvidhya.com — Memory Systems in AI Agents (April 2026) — Mem0/Zep/LangMem comparison
arxiv.org/abs/2504.19413 — Mem0 ECAI 2025 paper
arxiv.org/abs/2505.22101 — MemOS (short)
github.com/Shichun-Liu/Agent-Memory-Paper-List — curated academic paper list (updated Jan 2026)
github.com/BAI-LAB/MemoryOS — MemoryOS (EMNLP 2025)
code.claude.com/docs/en/overview — Claude Code memory docs
wavespeed.ai/blog/posts/claude-code-vs-cursor-2026 (April 2026) — post-leak Claude Code architecture analysis
arxiv.org/html/2602.10715 — LoCoMo-Plus (Feb 2026)
arxiv.org/html/2604.04514 — SuperLocalMemory V3.3 (Apr 2026)
arxiv.org/html/2604.02280 — Adaptive budgeted forgetting (Apr 2026)
---
codex:
Here’s a concise but comprehensive snapshot of the state of the art in AI agent memory (2025–2026), synthesizing production systems, recent papers, and emerging patterns.

1) Production Memory Systems (Framework Landscape)
Core trend: memory = separate infrastructure layer

By 2026, memory is no longer “chat history stuffing” — it’s a dedicated subsystem with its own APIs, benchmarks, and architectures .

Major frameworks
Mem0 (2025–2026)
Architecture: fact extraction → hybrid storage (vector + graph + KV)
Scopes: user / session / agent memory
Strength: production-ready, low latency, strong personalization
Weakness: limited temporal reasoning unless graph enabled
Benchmarks: ~67% accuracy vs 73% full-context, but ~90% cheaper/faster

👉 Canonical “selective memory pipeline”

Zep / Graphiti
Architecture: temporal knowledge graph
Each memory = structured fact with time validity
Retrieval: graph traversal + semantic + keyword
Strength: temporal reasoning, contradiction handling
Benchmark: up to +18.5% on temporal tasks

👉 Best-in-class for state evolution over time

Letta (MemGPT lineage)
Architecture: OS-inspired hierarchical memory
Core (in-context)
Recall (recent)
Archival (external)
Key idea: agent actively manages memory
Strength: long-running agents, autonomy

👉 Introduced self-editing memory paradigm

LangGraph / LangMem (LangChain ecosystem)
Memory types:
Episodic (interactions)
Semantic (facts)
Procedural (self-modifying prompts)
Strength: modular + ecosystem integration
Weakness: historically high latency in some configs

👉 First mainstream multi-type memory taxonomy

LlamaIndex memory
Primarily RAG-based memory
Chunking + embeddings + retrieval pipelines
Strength: simplicity, composability
Weakness: weaker long-horizon reasoning
Other notable systems
Microsoft Kernel Memory → enterprise RAG memory
MemX (2026) → local-first, stability-focused retrieval
MemMachine (2026) → episodic ground-truth preservation
Supermemory → optimized for coding agents
2) Academic State of the Art (2024–2026)
Key papers & ideas
Mem0 (ECAI 2025)
Introduces selective memory extraction
Shows:
Full-context = best accuracy but unusable cost
Selective memory = optimal tradeoff
Key result: 90% token reduction with small accuracy loss
Zep (2025)
Introduces temporal knowledge graph memory
Solves:
fact evolution
contradiction resolution
Outperforms RAG + MemGPT on temporal benchmarks
MemMachine (2026)
Critique: LLM-based summarization loses ground truth
Solution:
store full episodic traces
retrieve with context expansion
Result:
~93% accuracy on LongMemEval
80% fewer tokens than Mem0

👉 Shift toward lossless / minimally lossy memory

MemX (2026)
Focus: retrieval pipeline design over storage
Key innovations:
multi-signal retrieval (vector + keyword + rerank)
rejection of low-confidence recalls
Insight: retrieval quality > storage format
Meta-trend in research
Moving from:
“store embeddings”
→ toward:
structured + temporal + retrieval-optimized systems
3) Memory in Coding Agents (Claude Code, Cursor, Devin)
Common architecture (converging pattern)
1. Session memory
Current conversation / working context
Often partially summarized
2. Workspace memory
Codebase embeddings (RAG over files)
Indexed via:
AST-aware chunking
symbol-level retrieval
3. Persistent memory (lightweight)
Notes, instructions, preferences
Often:
flat key-value or embeddings
weak structure
Tool-specific tendencies
Claude Code
Strong context window + retrieval hybrid
Limited structured long-term memory (mostly notes/search)
Cursor
Emphasis on codebase RAG
Minimal cross-session memory
Devin (Cognition)
Likely uses:
task logs (episodic memory)
environment state tracking
tool outputs as memory
Emerging tools (Supermemory, etc.)
Add:
task-level memory
code reasoning traces

👉 Coding agents rely more on retrieval over environment state than user personalization memory.

4) Core Architectural Approaches
1. Vector-based (RAG memory)
Store: embeddings of chunks
Retrieve: similarity search
Pros: simple, scalable
Cons: weak structure, poor temporal reasoning
2. Hierarchical memory (MemGPT-style)
Layers:
working / short-term / long-term
Pros: aligns with cognition
Cons: complex control policies
3. Knowledge graph memory
Store: entities + relationships
Pros:
multi-hop reasoning
structured queries
Cons:
extraction complexity
schema drift
4. Temporal memory (Zep)
Add time dimension to facts
Pros:
resolves contradictions
tracks evolution
Cons:
higher modeling overhead
5. Summarization-based memory
Compress conversations → summaries
Pros: cheap
Cons: lossy → major failure source
6. Episodic / raw log memory (MemMachine)
Store full interactions
Retrieve with context expansion
Pros: highest fidelity
Cons: storage + retrieval complexity
7. Hybrid retrieval (emerging default)
Combine:
vector search
keyword (BM25)
graph traversal
reranking
Example: MemX, Zep, Hippocamp-style systems
5) Trade-offs
Approach	Recall	Cost	Freshness	Consistency
Full context	⭐⭐⭐⭐	❌ worst	⭐⭐⭐⭐	⭐⭐⭐⭐
Vector RAG	⭐⭐	⭐⭐⭐⭐	⭐⭐	⭐
Summaries	⭐	⭐⭐⭐⭐	⭐⭐	❌
Graph	⭐⭐⭐⭐	⭐⭐	⭐⭐⭐	⭐⭐⭐⭐
Temporal graph	⭐⭐⭐⭐	⭐⭐	⭐⭐⭐⭐	⭐⭐⭐⭐
Episodic (raw)	⭐⭐⭐⭐	⭐⭐	⭐⭐⭐⭐	⭐⭐⭐

Key insight:

Accuracy vs cost is the central trade-off
Full-context is upper bound but impractical
6) Common Failure Modes
1. Memory bloat
Too many low-value memories
Retrieval noise increases
2. Contradictory memories
“User likes X” vs “User dislikes X”
Vector stores can’t resolve this
3. Staleness
Old facts persist without invalidation
Fixed by temporal models (Zep)
4. Irrelevant retrieval
Semantic similarity ≠ relevance
Especially bad in long sessions
5. Lossy summarization
Critical detail dropped permanently
6. Over-retrieval
Too many memories → context dilution
7) Emerging Patterns (2025–2026)
1. Self-editing memory
Agents:
rewrite
delete
consolidate memory
Seen in:
Letta / LangMem procedural memory
2. Memory compaction
Merge redundant memories
Promote important ones
Analogous to:
garbage collection
3. Hybrid retrieval pipelines (dominant trend)
Vector + keyword + graph + reranker
Often with:
Reciprocal Rank Fusion
query rewriting
4. Temporal modeling
Facts become:
(entity, relation, value, time)
Enables:
history-aware reasoning
5. Episodic grounding
Shift away from:
extracted “facts”
Toward:
retrievable raw experience
6. Agent-controlled memory
Memory is no longer passive
Agents:
decide what to store
decide what to forget
8) Key Takeaways
1. No single dominant architecture

Instead:

Hybrid systems (graph + vector + episodic + reranking) are emerging as the default

2. Retrieval quality > storage format
MemX, MemMachine show:
retrieval pipeline dominates performance
3. Temporal + structured memory is the frontier
Zep-like systems solving:
contradiction
time evolution
4. Summarization-only memory is being phased out
Too lossy for long-term reasoning
5. The real bottleneck is:

selecting the right memories, not storing them

If you want, I can map this into a reference architecture (what to actually build in 2026) or compare which stack is best for your use case (coding agent, assistant, autonomous agent, etc.).
---
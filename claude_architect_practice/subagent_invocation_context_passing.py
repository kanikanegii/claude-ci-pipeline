"""Subagent invocation + context passing with structured metadata.

The failure this exercise is built around:

    synthesis agent emits claims with no citations
      -> people blame the synthesis PROMPT
      -> the real cause is the COORDINATOR stripping metadata before
         handing findings to synthesis.

An agent cannot cite a source it was never given. Attribution is a context
passing property, not a prompting property.

Exercise step -> code map:
  STEP 1  Task/Agent in allowedTools ............... build_coordinator_options, spawn_gate
  STEP 2  Two scoped subagent definitions .......... WEB_SEARCH_AGENT, DOC_ANALYSIS_AGENT
  STEP 3  Structured Finding schema ................ Finding, ResearchOutput, describe_schema
  STEP 4  Pass COMPLETE findings to synthesis ...... build_synthesis_prompt (lossy vs full)
  STEP 5  Verify every claim is attributable ....... verify_citations
  STEP 6  Parallel subagent spawn .................. coordinator_turn, compare_latency

Runs offline: the real SDK call is built and printed, then a simulator drives
the same shapes so every print below comes from the real functions.
"""

import json
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, asdict, fields

MODEL = "claude-opus-5"

# "Task" is the exam-guide name; renamed "Agent" in Claude Code v2.1.63.
# Both work - Task is kept as an alias.
SPAWN_TOOLS = ("Agent", "Task")


def step_banner(n: int, title: str, nudge: str, guidance: str, should_see: str) -> None:
    """Prints the exercise's own Nudge/Guidance next to the code that answers it."""
    print("\n" + "=" * 78)
    print(f"STEP {n}: {title}")
    print("-" * 78)
    print(f"  NUDGE     : {nudge}")
    print(f"  GUIDANCE  : {guidance}")
    print(f"  SHOULD SEE: {should_see}")
    print("=" * 78)


# ---------------------------------------------------------------------------
# STEP 2 - subagent definitions.
#
# An AgentDefinition is three things and no more:
#   description -> what the COORDINATOR reads to pick this agent
#   prompt      -> the subagent's system prompt (field is `prompt`, NOT
#                  `systemPrompt` - that name belongs to the top-level query)
#   tools       -> the scoped toolset; the agent cannot reach past it
# The agent's NAME is its key in options.agents, not a field on the object.
# ---------------------------------------------------------------------------

WEB_SEARCH_AGENT = {
    "description": (
        "Searches the web for current information and returns results with "
        "source URLs and titles"
    ),
    "prompt": (
        "Search for information on the given topic. Return each finding as JSON "
        "with fields: claim, source_url, source_title, retrieved_date, confidence. "
        "Never return a claim without a source_url."
    ),
    # Search only - this agent has no filesystem reach at all.
    "tools": ["WebSearch"],
}

DOC_ANALYSIS_AGENT = {
    "description": "Analyses documents and returns findings with page references",
    "prompt": (
        "Analyse the provided documents. Return each finding as JSON with fields: "
        "claim, document_name, page_number, section, confidence. "
        "Never return a claim without a document_name and page_number."
    ),
    # File reading only - no network.
    "tools": ["Read", "Grep"],
}

SYNTHESIS_AGENT = {
    "description": "Merges structured findings into a report where every claim is cited",
    "prompt": (
        "You are given structured findings as JSON. Write a report in which EVERY "
        "factual claim carries a citation drawn from the metadata on its finding "
        "(source_url, or document_name + page_number). If a finding lacks the "
        "metadata needed to cite it, say so explicitly rather than stating it bare."
    ),
    # No tools: synthesis reasons over what it was handed, it does not go fetch.
    "tools": [],
}


def describe_agent(name: str, definition: dict) -> None:
    print(f"\n  agents['{name}']")
    print(f"    description -> {definition['description']}")
    print(f"    prompt      -> {definition['prompt'][:72]}...")
    print(f"    tools       -> {definition['tools'] or '(none - reasons over passed context only)'}")
    print(f"    name        -> '{name}' comes from the DICT KEY, there is no name field")


# ---------------------------------------------------------------------------
# STEP 1 - the coordinator is a query() call, not a class.
# ---------------------------------------------------------------------------

def build_coordinator_options(include_spawn_tool: bool = True) -> dict:
    """Builds options for query(). The hard gate is Agent/Task in allowedTools."""
    allowed = ["Read"]
    if include_spawn_tool:
        # THE BINARY REQUIREMENT. Without this entry the coordinator physically
        # cannot invoke a subagent - options.agents is then dead configuration.
        allowed.insert(0, "Agent")

    options = {
        "systemPrompt": (
            "You coordinate research by delegating to specialist subagents and "
            "synthesising their findings. Pass each subagent's COMPLETE structured "
            "output downstream - never strip metadata."
        ),
        "model": MODEL,
        "allowedTools": allowed,
        "agents": {
            "web-search": WEB_SEARCH_AGENT,
            "doc-analysis": DOC_ANALYSIS_AGENT,
            "synthesis": SYNTHESIS_AGENT,
        },
    }
    print(f"  allowedTools        -> {options['allowedTools']}")
    print(f"  agents defined      -> {list(options['agents'])}")
    return options


def spawn_gate(options: dict) -> bool:
    """Can this coordinator spawn subagents at all? One check, no fallback."""
    allowed = options["allowedTools"]
    can = any(t in allowed for t in SPAWN_TOOLS)
    print(f"  spawn_gate: looking for one of {list(SPAWN_TOOLS)} in {allowed}")
    if can:
        which = next(t for t in SPAWN_TOOLS if t in allowed)
        print(f"    FOUND '{which}' -> coordinator CAN spawn "
              f"{len(options['agents'])} subagents")
    else:
        print(f"    NOT FOUND -> coordinator CANNOT spawn anything.")
        print(f"    The {len(options['agents'])} agents under options.agents are dead config:")
        print(f"    defined, selectable, and unreachable. There is no fallback path,")
        print(f"    no runtime flag, no implicit grant. The run silently does nothing.")
    return can


# ---------------------------------------------------------------------------
# STEP 3 - the structured contract: content separated from attribution.
# ---------------------------------------------------------------------------

CONTENT_FIELDS = {"claim"}
METADATA_FIELDS = {"source_url", "source_title", "document_name", "page_number",
                   "confidence", "retrieved_by", "retrieved_date"}

# WHAT IS REQUIRED FOR FULL ATTRIBUTION.
#
# Not all 7 metadata fields are required - they are not equal. A citation is
# built from ONE of these recipes, and a recipe is satisfied only when ALL of
# its `requires` fields are present. Written as data, not as if-statements, so
# the requirement is something you can print and inspect rather than something
# buried in control flow.
CITATION_RECIPES = [
    {
        "name": "web",
        "requires": ["source_url"],                    # the hard requirement
        "optional": ["source_title"],                  # improves it, not required
        "format": lambda f: f"[{f.source_title or 'source'}]({f.source_url})",
    },
    {
        "name": "document",
        "requires": ["document_name", "page_number"],  # BOTH, or you cannot cite
        "optional": [],
        "format": lambda f: f"{f.document_name}, p.{f.page_number}",
    },
]

# Fields that carry no citation on their own. They qualify a claim rather than
# locate it - useful downstream, but their absence never blocks a citation.
SUPPORTING_FIELDS = ["confidence", "retrieved_by", "retrieved_date"]


@dataclass
class Finding:
    # --- content: what is being asserted -------------------------------
    claim: str
    # --- metadata: everything needed to ATTRIBUTE that assertion -------
    source_url: str | None
    source_title: str | None
    document_name: str | None
    page_number: int | None
    confidence: str          # "high" | "medium" | "low"
    retrieved_by: str        # which subagent produced this
    retrieved_date: str

    def has(self, field_name: str) -> bool:
        """Present means present - None and empty string both mean 'not given'."""
        value = getattr(self, field_name)
        return value is not None and value != ""

    def citation(self, trace: bool = False) -> str | None:
        """HOW A CITATION IS CREATED.

        Try each recipe in order. A recipe wins when every field it `requires`
        is present on this finding; its `format` then turns those fields into
        citation text. If no recipe is satisfied, the claim CANNOT be cited -
        and that is a data problem, never a wording problem.
        """
        for recipe in CITATION_RECIPES:
            present = [r for r in recipe["requires"] if self.has(r)]
            missing = [r for r in recipe["requires"] if not self.has(r)]
            if trace:
                verdict = "SATISFIED" if not missing else f"missing {missing}"
                print(f"        recipe '{recipe['name']}' requires {recipe['requires']}"
                      f" -> has {present} -> {verdict}")
            if missing:
                continue
            text = recipe["format"](self)
            if trace:
                used = {r: getattr(self, r) for r in recipe["requires"]}
                extra = {o: getattr(self, o) for o in recipe["optional"] if self.has(o)}
                print(f"        building from {used}" + (f" + optional {extra}" if extra else ""))
                print(f"        -> '{text}'")
            return text
        if trace:
            print(f"        no recipe satisfied -> citation() returns None")
            print(f"        this claim CANNOT be cited. The synthesis agent is not at")
            print(f"        fault - it was handed a claim with no locator attached.")
        return None


@dataclass
class ResearchOutput:
    findings: list[Finding]
    query: str
    timestamp: str


def describe_schema() -> None:
    names = [f.name for f in fields(Finding)]
    content = [n for n in names if n in CONTENT_FIELDS]
    meta = [n for n in names if n in METADATA_FIELDS]
    print(f"  Finding has {len(names)} fields, and the split is the whole design:")
    print(f"    CONTENT  ({len(content)}) -> {content}")
    print(f"       what the report SAYS")
    print(f"    METADATA ({len(meta)}) -> {meta}")
    print(f"       what makes it CITABLE - and what a lossy coordinator drops")


def describe_requirements() -> None:
    """Answers 'what is required for full attribution' with the actual rule."""
    print(f"  A citation needs ONE of these recipes fully satisfied:")
    for recipe in CITATION_RECIPES:
        req = " AND ".join(recipe["requires"])
        print(f"    recipe '{recipe['name']:8}' REQUIRES: {req}")
        if recipe["optional"]:
            print(f"    {'':17} optional: {recipe['optional']} (improves the text, not required)")
    print(f"\n  So the minimum for attribution is small: source_url on its own,")
    print(f"  OR document_name + page_number together. Everything else is extra.")
    print(f"\n  The remaining metadata fields are SUPPORTING, not required:")
    for name in SUPPORTING_FIELDS:
        print(f"    {name:15} -> never blocks a citation, but the report loses information without it")
    print(f"  retrieved_by is the one people forget: it tells synthesis WHICH")
    print(f"  recipe to expect, so it can flag a web finding that arrived with no URL.")


def show_citation_build(f: Finding) -> None:
    """Runs citation() out loud so the construction is visible, not just its result."""
    print(f"\n    claim        : {f.claim}")
    print(f"    retrieved_by : {f.retrieved_by}")
    filled = [n for n in METADATA_FIELDS if f.has(n)]
    empty = [n for n in METADATA_FIELDS if not f.has(n)]
    print(f"    metadata present: {sorted(filled)}")
    print(f"    metadata absent : {sorted(empty)}")
    print(f"    citation() walking the recipes:")
    result = f.citation(trace=True)
    print(f"    RESULT -> {result!r}")


# ---------------------------------------------------------------------------
# STEP 6 - parallel spawn. The parallelism lives in ONE model turn: the
# coordinator emits both Agent tool_use blocks in a single assistant message
# and the SDK runs them concurrently. There is no Promise.all / asyncio.gather
# in your code - you steer it with the prompt.
# ---------------------------------------------------------------------------

COORDINATOR_PROMPT = (
    "Research the topic. Invoke the web-search and doc-analysis subagents in "
    "PARALLEL - emit both Agent tool calls in a single response, do not wait for "
    "one before starting the other. When both return, pass their COMPLETE "
    "structured findings, every metadata field intact, to the synthesis subagent."
)


def fake_subagent(agent_name: str, seconds: float) -> ResearchOutput:
    """Stands in for a real subagent run; the sleep is real so the wall clock
    below is a real measurement, not a claim."""
    print(f"       [{agent_name}] running (~{seconds}s)...")
    time.sleep(seconds)
    out = FAKE_RESULTS[agent_name]
    print(f"       [{agent_name}] returned {len(out.findings)} findings")
    return out


def coordinator_turn(assistant_blocks: list[dict]) -> dict[str, ResearchOutput]:
    """One assistant turn. Two tool_use blocks in the SAME message = concurrent."""
    tool_uses = [b for b in assistant_blocks if b["type"] == "tool_use"]
    print(f"  assistant message carries {len(assistant_blocks)} blocks, "
          f"{len(tool_uses)} of them tool_use:")
    for b in tool_uses:
        print(f"    tool_use id={b['id']} name={b['name']} subagent_type={b['input']['subagent_type']}")

    if len(tool_uses) > 1:
        print(f"  -> {len(tool_uses)} spawn calls in ONE message: the SDK runs them CONCURRENTLY")
    else:
        print(f"  -> 1 spawn call in this message: the next one waits for a whole extra turn")

    start = time.time()
    with ThreadPoolExecutor(max_workers=len(tool_uses)) as pool:
        futures = {
            b["input"]["subagent_type"]: pool.submit(
                fake_subagent, b["input"]["subagent_type"], FAKE_DURATIONS[b["input"]["subagent_type"]]
            )
            for b in tool_uses
        }
        results = {name: f.result() for name, f in futures.items()}
    print(f"  turn wall clock: {time.time() - start:.2f}s")
    return results


def compare_latency() -> None:
    """Same two subagents, sequential vs parallel, actually timed."""
    a, b = "web-search", "doc-analysis"
    print(f"  SEQUENTIAL - one Agent call, wait, then the next (two model turns):")
    t0 = time.time()
    fake_subagent(a, FAKE_DURATIONS[a])
    fake_subagent(b, FAKE_DURATIONS[b])
    seq = time.time() - t0
    print(f"    total {seq:.2f}s  (= {FAKE_DURATIONS[a]} + {FAKE_DURATIONS[b]}, they ADD)")

    print(f"\n  PARALLEL - both Agent calls in one assistant message:")
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda n: fake_subagent(n, FAKE_DURATIONS[n]), [a, b]))
    par = time.time() - t0
    print(f"    total {par:.2f}s  (= max({FAKE_DURATIONS[a]}, {FAKE_DURATIONS[b]}), they OVERLAP)")
    print(f"\n  saved {seq - par:.2f}s. Valid here ONLY because the two tasks are")
    print(f"  INDEPENDENT - neither reads the other's output. Synthesis is NOT")
    print(f"  independent, so it stays in a later turn.")


# ---------------------------------------------------------------------------
# STEP 4 - the step the exam actually targets. Two ways to hand findings to
# the synthesis agent; only one of them can produce a cited report.
# ---------------------------------------------------------------------------

def build_synthesis_prompt(web: ResearchOutput, docs: ResearchOutput, lossy: bool) -> str:
    """lossy=True is the bug: summarising findings down to claim strings.
    lossy=False is the fix: the COMPLETE structured objects, verbatim."""
    if lossy:
        # THE BUG. Looks harmless, reads like tidying up, destroys attribution.
        web_block = json.dumps([f.claim for f in web.findings], indent=2)
        docs_block = json.dumps([f.claim for f in docs.findings], indent=2)
        print(f"    LOSSY: extracted .claim only -> {len(CONTENT_FIELDS)} of "
              f"{len(fields(Finding))} fields survive")
        print(f"    dropped: {sorted(METADATA_FIELDS & {f.name for f in fields(Finding)})}")
    else:
        # THE FIX. Full objects, every metadata field intact, no summarising.
        web_block = json.dumps([asdict(f) for f in web.findings], indent=2)
        docs_block = json.dumps([asdict(f) for f in docs.findings], indent=2)
        print(f"    FULL: complete objects -> all {len(fields(Finding))} fields survive")
        print(f"    dropped: nothing")

    prompt = (
        "Synthesise the following research findings into a coherent report. "
        "Every claim MUST include a citation with source URL or document + page number.\n\n"
        f"Web search findings ({len(web.findings)} findings, retrieved_by=web-search):\n"
        f"{web_block}\n\n"
        f"Document analysis findings ({len(docs.findings)} findings, retrieved_by=doc-analysis):\n"
        f"{docs_block}\n\n"
        "Output a report where every factual claim links to its source."
    )
    print(f"    prompt size: {len(prompt)} chars")
    return prompt


def synthesise(prompt: str) -> str:
    """Stands in for the synthesis subagent, and models it honestly: it can only
    cite what the prompt actually contains. Given claim strings it writes bare
    claims; given full objects it writes citations. No prompt wording changes
    this - the information is either there or it is not."""
    web_start = prompt.index("Web search findings")
    payload = prompt[web_start:]
    has_metadata = '"source_url"' in payload or '"document_name"' in payload
    print(f"    synthesis agent sees metadata in its prompt: {has_metadata}")

    # Pull every claim the prompt carries, in either shape.
    claims = []
    for chunk in payload.splitlines():
        chunk = chunk.strip().rstrip(",")
        if chunk.startswith('"claim": "'):
            claims.append(chunk[len('"claim": "'):-1])
        elif chunk.startswith('"') and chunk.endswith('"') and ":" not in chunk:
            claims.append(chunk.strip('"'))

    lines = ["# Research report", ""]
    for claim in claims:
        if has_metadata:
            match = next((f for f in ALL_FINDINGS if f.claim == claim), None)
            cite = match.citation() if match else None
            lines.append(f"- {claim} ({cite})" if cite else f"- {claim}")
        else:
            # Nothing to cite with. The agent is not being lazy; it was starved.
            lines.append(f"- {claim}")
    report = "\n".join(lines)
    print(f"    synthesis agent wrote {len(claims)} claims, {len(report)} chars")
    return report


# ---------------------------------------------------------------------------
# STEP 5 - verification. Every claim must be traceable back to a source.
# ---------------------------------------------------------------------------

def verify_citations(report: str, findings: list[Finding]) -> list[str]:
    """Walks the report claim by claim and asks: is a citation present for it?
    Uncited claims are NOT a synthesis-prompt problem - trace back to whether
    the metadata was in the input at all."""
    print(f"  checking {len(findings)} known findings against a {len(report)}-char report")
    uncited = []
    for f in findings:
        if f.claim not in report:
            continue
        line = next(l for l in report.splitlines() if f.claim in l)
        cite = f.citation()
        present = cite is not None and cite in line
        mark = "cited  " if present else "UNCITED"
        print(f"    {mark} | {f.claim[:44]:44} | expected: {cite}")
        if not present:
            uncited.append(f.claim)
    total = sum(1 for f in findings if f.claim in report)
    print(f"  RETURNS -> {len(uncited)} uncited of {total} claims "
          f"({(total - len(uncited)) / (total or 1):.0%} attributable)")
    return uncited


def diagnose(uncited: list[str], synthesis_prompt: str) -> None:
    """The diagnostic the exam is really testing."""
    if not uncited:
        print("  all claims attributable - context passing preserved the metadata")
        return
    print(f"  {len(uncited)} uncited claims. Root-cause order matters:")
    metadata_in_prompt = '"source_url"' in synthesis_prompt or '"document_name"' in synthesis_prompt
    print(f"    1. was metadata in the synthesis input? -> {metadata_in_prompt}")
    if not metadata_in_prompt:
        print(f"    2. NO. So this is a COORDINATOR bug, not a synthesis-prompt bug.")
        print(f"       Rewording the synthesis prompt cannot fix it. The agent was")
        print(f"       handed claim strings; it had nothing to cite with.")
        print(f"       FIX: pass the complete Finding objects, not [f.claim for f in ...]")
    else:
        print(f"    2. YES - metadata was present, so now the synthesis prompt is fair game.")


# ---------------------------------------------------------------------------
# Fake data - no API key, no network. Every print above comes from the real
# functions; this block only supplies what the network would have.
# ---------------------------------------------------------------------------

WEB_FINDINGS = [
    Finding("Global solar capacity passed 1.6 TW in 2024",
            "https://example.com/solar-2024", "Solar Outlook 2024", None, None,
            "high", "web-search", "2026-09-13"),
    Finding("Offshore wind costs fell 12% year over year",
            "https://example.com/wind-costs", "Wind Cost Index", None, None,
            "medium", "web-search", "2026-09-13"),
]
DOC_FINDINGS = [
    Finding("Internal pilot achieved 34% grid-storage round-trip efficiency",
            None, None, "storage_pilot_report.pdf", 12, "high", "doc-analysis", "2026-09-13"),
    Finding("Procurement lead time for turbines averages 14 months",
            None, None, "supply_chain_review.pdf", 41, "medium", "doc-analysis", "2026-09-13"),
]
ALL_FINDINGS = WEB_FINDINGS + DOC_FINDINGS

FAKE_RESULTS = {
    "web-search": ResearchOutput(WEB_FINDINGS, "renewable energy", "2026-09-13T10:00:00Z"),
    "doc-analysis": ResearchOutput(DOC_FINDINGS, "renewable energy", "2026-09-13T10:00:00Z"),
}
FAKE_DURATIONS = {"web-search": 0.8, "doc-analysis": 0.5}


if __name__ == "__main__":
    # -- STEP 1 ------------------------------------------------------------
    step_banner(
        1, "Coordinator with Agent (Task) in allowedTools",
        nudge="Omit Task/Agent from allowedTools and the coordinator simply cannot "
              "spawn subagents - there is no fallback.",
        guidance="There is no Agent class to instantiate. The coordinator IS a query() "
                 "call: Agent goes in options.allowedTools, subagents go in options.agents.",
        should_see="allowedTools explicitly containing Agent (or Task), plus subagent "
                   "definitions under options.agents.",
    )
    print("\n  -- the broken version first, so the gate is visible --")
    broken = build_coordinator_options(include_spawn_tool=False)
    spawn_gate(broken)

    print("\n  -- the correct version --")
    options = build_coordinator_options(include_spawn_tool=True)
    spawn_gate(options)

    print("\n  the query() call this maps to:")
    print("    query(prompt=COORDINATOR_PROMPT, options=ClaudeAgentOptions(")
    print(f"      allowed_tools={options['allowedTools']},")
    agents_repr = ", ".join(f"{k!r}: AgentDefinition(...)" for k in options["agents"])
    print(f"      agents={{{agents_repr}}},")
    print("    ))")

    # # -- STEP 2 ------------------------------------------------------------
    step_banner(
        2, "Two scoped subagents: web search and document analysis",
        nudge="What three things does an AgentDefinition specify? Think about how the "
              "coordinator uses each one.",
        guidance="description (coordinator reads it to SELECT), prompt (the subagent's "
                 "system prompt - the field is `prompt`, not `systemPrompt`), tools "
                 "(scoped to the role). The name is the key in options.agents.",
        should_see="Two AgentDefinitions with restricted tool sets: search tools only "
                   "vs file reading only.",
    )
    for name in ("web-search", "doc-analysis", "synthesis"):
        describe_agent(name, options["agents"][name])
    print(f"\n  tool scoping is not decoration:")
    print(f"    web-search  has {WEB_SEARCH_AGENT['tools']} -> cannot touch the filesystem")
    print(f"    doc-analysis has {DOC_ANALYSIS_AGENT['tools']} -> cannot reach the network")
    print(f"    neither can spawn: 'Agent' is in NEITHER list, only the coordinator's")

    # # -- STEP 3 ------------------------------------------------------------
    step_banner(
        3, "Structured output separating content from metadata",
        nudge="What fields does the synthesis agent need to produce a properly cited "
              "report? Think about what full attribution requires.",
        guidance="The finding must carry enough metadata for ANY downstream agent to "
                 "build a citation without going back to the source: source_url, "
                 "document_name, page_number, confidence, retrieved_by.",
        should_see="A Finding type with both content fields and metadata fields.",
    )
    describe_schema()

    print(f"\n  -- WHAT IS REQUIRED FOR FULL ATTRIBUTION --")
    describe_requirements()

    print(f"\n  -- HOW A CITATION IS ACTUALLY CREATED --")
    print(f"  citation() tries each recipe in order and stops at the first one whose")
    print(f"  required fields are ALL present. Watch it decide, three times:")

    print(f"\n  [A] a web finding - recipe 'web' should win on source_url")
    show_citation_build(WEB_FINDINGS[0])

    print(f"\n  [B] a document finding - no source_url, so 'web' fails and 'document' wins")
    show_citation_build(DOC_FINDINGS[0])

    print(f"\n  [C] the same web claim AFTER a lossy coordinator stripped its metadata")
    print(f"      (this is exactly what STEP 4's bug produces)")
    stripped = Finding(claim=WEB_FINDINGS[0].claim, source_url=None, source_title=None,
                       document_name=None, page_number=None, confidence="",
                       retrieved_by="", retrieved_date="")
    show_citation_build(stripped)
    print(f"\n  [A] and [C] carry the IDENTICAL claim text. One is citable, one is not.")
    print(f"  The difference is metadata, and metadata is the coordinator's job to carry.")


    # -- STEP 4 ------------------------------------------------------------
    # NOTE ON ORDER: this runs BEFORE step 6, matching the exercise. Step 4 only
    # needs findings that EXIST - it does not care whether the research agents
    # ran in parallel, ran sequentially, or came from a cache. Taking them from
    # the research stage's output rather than from a particular spawn strategy
    # is what lets step 6 stay a refactor.
    step_banner(
        4, "Pass COMPLETE structured results to the synthesis subagent",
        nudge="Check you are passing the whole structured object, not just the claim "
              "strings. What happens if you only pass the claims?",
        guidance="The synthesis prompt must include the full JSON of every finding from "
                 "both subagents, verbatim. Do not summarise, do not select a subset "
                 "of fields.",
        should_see="The complete findings array with all metadata intact in the "
                   "synthesis prompt.",
    )
    web_out, doc_out = FAKE_RESULTS["web-search"], FAKE_RESULTS["doc-analysis"]
    print(f"  coordinator holds {len(web_out.findings)} web + {len(doc_out.findings)} doc "
          f"findings. HOW they were fetched is not this step's concern.")

    print("\n  --- PATH A: the bug. Coordinator 'tidies up' before passing on. ---")
    lossy_prompt = build_synthesis_prompt(web_out, doc_out, lossy=True)
    lossy_report = synthesise(lossy_prompt)
    print("\n  report produced:")
    for line in lossy_report.splitlines():
        print(f"    {line}")

    print("\n  --- PATH B: the fix. Complete objects, nothing stripped. ---")
    full_prompt = build_synthesis_prompt(web_out, doc_out, lossy=False)
    full_report = synthesise(full_prompt)
    print("\n  report produced:")
    for line in full_report.splitlines():
        print(f"    {line}")

    print(f"\n  same findings, same synthesis prompt wording, same model.")
    print(f"  the ONLY difference is what the coordinator passed:")
    print(f"    lossy prompt: {len(lossy_prompt)} chars")
    print(f"    full prompt:  {len(full_prompt)} chars")

    # # -- STEP 5 ------------------------------------------------------------
    step_banner(
        5, "Verify every claim is attributable to a specific source",
        nudge="How do you check this programmatically? Look for a pattern: each claim "
              "should sit on a line that also carries its source.",
        guidance="Parse the output and check each claim references at least one source. "
                 "Flag the ones that do not, and trace back to whether the metadata was "
                 "present in the input - do not blame the synthesis agent first.",
        should_see="Every factual claim carrying a citation; no orphaned claims.",
    )
    print("\n  verifying PATH A (lossy):")
    uncited_a = verify_citations(lossy_report, ALL_FINDINGS)
    diagnose(uncited_a, lossy_prompt)

    print("\n  verifying PATH B (full):")
    uncited_b = verify_citations(full_report, ALL_FINDINGS)
    diagnose(uncited_b, full_prompt)

    # # -- STEP 6 ------------------------------------------------------------
    # LAST, because the exercise says "REFACTOR the coordinator". A refactor is
    # only a refactor if the thing already worked. Steps 1-5 above produced a
    # correct, fully cited report with no parallelism anywhere; step 6 changes
    # how the findings are fetched and NOTHING downstream.
    step_banner(
        6, "Refactor: spawn both research subagents in PARALLEL",
        nudge="What makes these two tasks suitable for parallel execution? They are "
              "independent - neither needs the other's result.",
        guidance="Emit both Agent tool calls in a SINGLE assistant response. The "
                 "parallelism happens inside one model turn; the SDK runs them "
                 "concurrently. No asyncio.gather in your code - steer it via the prompt.",
        should_see="Both subagents invoked simultaneously, coordinator waiting for both "
                   "before synthesis.",
    )
    compare_latency()

    print(f"\n  now the real turn - both tool_use blocks in ONE assistant message:")
    turn = [
        {"type": "text", "text": "Spawning both research subagents now."},
        {"type": "tool_use", "id": "toolu_01", "name": "Agent",
         "input": {"subagent_type": "web-search", "prompt": "Search renewable energy trends"}},
        {"type": "tool_use", "id": "toolu_02", "name": "Agent",
         "input": {"subagent_type": "doc-analysis", "prompt": "Analyse the internal PDFs"}},
    ]
    results = coordinator_turn(turn)

    # The proof that this was a REFACTOR and not a rewrite: feed the parallel
    # results through the same untouched synthesis path and compare.
    print(f"\n  re-running synthesis on the PARALLEL results, same code path:")
    refactored_report = synthesise(
        build_synthesis_prompt(results["web-search"], results["doc-analysis"], lossy=False)
    )
    print(f"\n  identical to the step 4 report? {refactored_report == full_report}")
    print(f"  that is the whole test for a latency refactor: the OUTPUT must not move.")
    print(f"  parallelism is allowed to change WHEN findings arrive, never WHAT they say.")
    print(f"  had synthesis been coupled to the spawn strategy, this would not hold.")

    # -- SUMMARY -----------------------------------------------------------
    print("\n" + "=" * 78)
    print("SUMMARY")
    print("-" * 78)
    print(f"  Agent/Task in allowedTools      -> gate passed: "
          f"{any(t in options['allowedTools'] for t in SPAWN_TOOLS)}")
    print(f"  subagents defined               -> {list(options['agents'])}")
    print(f"  citation recipes                -> {[r['name'] for r in CITATION_RECIPES]}")
    print(f"  lossy path uncited claims       -> {len(uncited_a)} of {len(ALL_FINDINGS)}")
    print(f"  full path uncited claims        -> {len(uncited_b)} of {len(ALL_FINDINGS)}")
    print(f"  parallel refactor output moved  -> {refactored_report != full_report}")
    print(f"\n  The lesson: attribution failures are CONTEXT PASSING failures.")
    print(f"  Both paths used the identical synthesis prompt. Only one could cite,")
    print(f"  because only one was given anything to cite with.")
    print("=" * 78)
